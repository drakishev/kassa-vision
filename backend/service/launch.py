"""Own exactly two child processes; never stop unrelated GPU/API processes."""
import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time

from .config import ROOT, Settings


def choose_gpu(requested, inventory=None):
    if inventory is None:
        inventory = subprocess.check_output(["nvidia-smi", "--query-gpu=index,memory.free",
            "--format=csv,noheader,nounits"], text=True, timeout=10)
    available = {}
    for line in inventory.strip().splitlines():
        index, free = line.split(",")
        available[index.strip()] = int(free.strip())
    candidates = {index: free for index, free in available.items() if free >= 80*1024}
    if requested == "auto":
        if not candidates:
            raise ValueError("No GPU has at least 80 GiB free for this VLM")
        return max(candidates, key=lambda index: (candidates[index], -int(index)))
    if requested not in candidates:
        raise ValueError(f"GPU {requested} does not have at least 80 GiB free")
    return requested


def process_start(pid):
    return open(f"/proc/{pid}/stat").read().split(") ", 1)[1].split()[19]


def stop_service():
    settings = Settings()
    path = settings.runtime/"service.pid.json"
    if not path.exists():
        print("No saved service process")
        return
    record = json.loads(path.read_text())
    pid = record["pid"]
    try:
        same_process = process_start(pid) == record["start_ticks"]
        command = open(f"/proc/{pid}/cmdline", "rb").read()
        if not same_process or b"service.launch" not in command:
            raise RuntimeError("Saved PID no longer belongs to this service")
        os.kill(pid, signal.SIGTERM)
        print("Shutdown requested for this service")
    except FileNotFoundError:
        path.unlink(missing_ok=True)
        print("Service is already stopped")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gpu", default="auto")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if args.stop:
        stop_service()
        return
    settings = Settings()
    lock = (settings.runtime/"launcher.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Service is already running")
    # Fail before allocating GPU memory if another project owns this port.
    with socket.socket() as probe:
        # Match uvicorn's bind behavior: TIME_WAIT from our preceding process is
        # not an occupied listener and must not prevent an immediate restart.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((args.host, args.port))
        except OSError as exc:
            raise SystemExit(f"Port {args.port} is unavailable; choose another --port") from exc
    try:
        selected_gpu = choose_gpu(args.gpu)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    pid_path = settings.runtime/"service.pid.json"
    pid_path.write_text(json.dumps({"pid": os.getpid(), "start_ticks": process_start(os.getpid()),
                                    "host": args.host, "port": args.port, "gpu": selected_gpu})+"\n")
    children, stopping = [], False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    for signum in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(signum, request_stop)
    try:
        children.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "service.api:create_app",
            "--factory", "--host", args.host, "--port", str(args.port)], cwd=ROOT))
        worker_python = os.environ.get('KASSA_WORKER_PYTHON', sys.executable)
        children.append(subprocess.Popen([worker_python, "-m", "service.worker", "--gpu", selected_gpu], cwd=ROOT))
        print(json.dumps({"api_url": f"http://{args.host}:{args.port}",
                          "key_file": str(settings.runtime / "api.key"), "worker_pid": children[1].pid}), flush=True)
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(0.5)
        if not stopping:
            raise RuntimeError('API or worker exited unexpectedly')
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        # A GPU call may take time to return; killing only our child leaves its
        # running job in SQLite for recovery by the next lock-holding worker.
        end = time.monotonic()+15
        for child in children:
            try:
                child.wait(timeout=max(0.1, end-time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        pid_path.unlink(missing_ok=True)
        lock.close()


if __name__ == "__main__":
    main()
