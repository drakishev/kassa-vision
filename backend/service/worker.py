import argparse
import fcntl
import json
import os
import signal
import threading
import time
import traceback

from .config import Settings
from .jobs import JobStore
from .cancellation import AnalysisCancelled


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", default="1")
    parser.add_argument("--fps", type=float, default=1)
    args = parser.parse_args()
    if not 0 < args.fps <= 5:
        parser.error("Worker sampling fps must be in (0,5]")
    os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu
    settings = Settings(fps=args.fps)
    store = JobStore(settings.database)
    lock = (settings.runtime/"worker.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Another worker already owns this queue")
    stop = threading.Event()
    state = {"state": "loading"}
    for signum in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(signum, lambda *_: stop.set())

    def heartbeat():
        while not stop.is_set():
            store.heartbeat(state["state"])
            stop.wait(3)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        store.recover()
        from .inference import InferenceEngine
        engine = InferenceEngine(settings)
        def release_temporary():
            if hasattr(engine, 'release_temporary'):
                engine.release_temporary()
            else:
                engine.vlm.torch.cuda.empty_cache()
        state["state"] = "ready"
        print(json.dumps({"stage": "worker_ready", "model_load_sec": engine.load_sec}), flush=True)
        while not stop.is_set():
            job = store.claim()
            if job is None:
                stop.wait(0.25)
                continue
            state["state"] = "busy"
            print(json.dumps({"stage": "job_start", "job_id": job["job_id"], "attempt": job["attempts"]}), flush=True)
            try:
                def check_cancelled():
                    if store.is_cancelled(job['job_id']):raise AnalysisCancelled()
                result = engine.analyze(job,check_cancelled=check_cancelled)
                store.finish(job["job_id"], result)
                print(json.dumps({"stage": "job_done", "job_id": job["job_id"]}), flush=True)
            except AnalysisCancelled:
                print(json.dumps({"stage":"job_cancelled","job_id":job['job_id']}),flush=True)
                release_temporary()
            except Exception as exc:
                traceback.print_exc()
                message = "Analysis exceeded its processing time budget" if isinstance(exc, TimeoutError) else "Local video analysis failed; see worker log"
                store.fail(job["job_id"], message)
                print(json.dumps({"stage": "job_failed", "job_id": job["job_id"],
                                  "error_type": type(exc).__name__}), flush=True)
                # Release temporary CUDA allocations after an OOM before trying another job.
                release_temporary()
            state["state"] = "ready"
    finally:
        stop.set()
        thread.join(timeout=5)
        store.heartbeat("offline")
        lock.close()


if __name__ == "__main__":
    main()
