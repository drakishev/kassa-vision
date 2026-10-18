"""Resume disjoint local inference shards; completed manifests are skipped."""
import sys,argparse,fcntl,json,os,subprocess,urllib.request,sqlite3
from pathlib import Path
from annotation_service.paths import dataset_path, runtime_path, source_records
ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--detector-gpu',required=True);p.add_argument('--vlm-urls',nargs='+',required=True);p.add_argument('--workers',type=int,default=4);args=p.parse_args()
    if not 1<=args.workers<=8:p.error('workers must be between 1 and 8')
    from .vlm_client import LocalVLMClient
    for url in args.vlm_urls:
        LocalVLMClient(url)
        with urllib.request.urlopen(url.rstrip('/')+'/health',timeout=10) as r:assert r.status==200
    run=Path(args.run).resolve();run.mkdir(parents=True,exist_ok=True)
    with (run/'batch.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # A prior detached worker must not be overwritten on resume.
        for file in run.glob('worker_*_process.json'):
            proc=json.loads(file.read_text());cmd=Path(f'/proc/{proc["pid"]}/cmdline')
            if cmd.exists() and b'annotation_service.preannotation.worker' in cmd.read_bytes():raise RuntimeError(f'Worker {proc["pid"]} still running')
        if not (run/'before.sqlite3').exists():
            with sqlite3.connect(runtime_path(ROOT)/'annotations.sqlite3') as source,sqlite3.connect(run/'before.sqlite3') as backup:source.backup(backup)
        jobs=[]
        try:
            for shard in range(args.workers):
                cmd=[sys.executable,'-m','annotation_service.preannotation.worker','--run',str(run),'--shard',str(shard),'--shards',str(args.workers),'--vlm-url',args.vlm_urls[shard%len(args.vlm_urls)]]
                env=os.environ.copy();env.update(CUDA_VISIBLE_DEVICES=args.detector_gpu,OMP_NUM_THREADS='6')
                with (run/f'worker_{shard}.log').open('ab') as log:job=subprocess.Popen(cmd,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
                jobs.append(job);(run/f'worker_{shard}_process.json').write_text(json.dumps({'pid':job.pid,'gpu':args.detector_gpu,'cmd':cmd}))
            codes=[j.wait() for j in jobs]
        finally:
            for j in jobs:
                if j.poll() is None:j.terminate()
        from .common import video_id
        expected=source_records(ROOT)
        done=sum((m:=run/video_id(r['filename'])/'manifest.json').exists() and json.loads(m.read_text()).get('status')=='done' for r in expected)
        print(json.dumps({'completed':done,'expected':len(expected),'worker_exit_codes':codes}));raise SystemExit(0 if done==len(expected) and not any(codes) else 1)
if __name__=='__main__':main()
