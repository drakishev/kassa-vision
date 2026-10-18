"""Own-process-only lifecycle helper for the localhost workstation."""
import argparse,fcntl,json,os,signal,socket,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=Path(os.environ.get('ANNOTATION_RUNTIME',ROOT/'data/annotation_service')).resolve()
def alive(info):
    try:
        pid=int(info['pid']);cmd=Path(f'/proc/{pid}/cmdline').read_bytes();start=Path(f'/proc/{pid}/stat').read_text().split()[21]
        return b'annotation_service.app:create_app' in cmd and str(info['port']).encode() in cmd and start==info['start']
    except (OSError,KeyError,ValueError,IndexError):return False

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['start','stop','status'],nargs='?',default='start');p.add_argument('--port',type=int,default=18862);a=p.parse_args()
    RUNTIME.mkdir(parents=True,exist_ok=True);pidfile=RUNTIME/'server.json'
    with (RUNTIME/'launch.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:info=json.loads(pidfile.read_text())
        except (OSError,ValueError):info={}
        running=alive(info)
        if a.action=='status':
            print(f"Running: http://127.0.0.1:{info['port']} (PID {info['pid']})" if running else 'Not running');return 0 if running else 1
        if a.action=='stop':
            if running:
                os.kill(info['pid'],signal.SIGTERM)
                for _ in range(100):
                    if not alive(info):break
                    time.sleep(.1)
                if alive(info):print('Still stopping; no forced kill issued.');return 1
            pidfile.unlink(missing_ok=True);print('Stopped');return 0
        if running:print(f"Already running: http://127.0.0.1:{info['port']}");return 0
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            try:sock.bind(('127.0.0.1',a.port))
            except OSError:print(f'Port {a.port} is already in use. Choose --port.');return 1
        with (RUNTIME/'server.log').open('ab') as log:
            proc=subprocess.Popen([sys.executable,'-m','uvicorn','annotation_service.app:create_app','--factory','--host','127.0.0.1','--port',str(a.port),'--no-access-log'],cwd=ROOT,env={**os.environ,'ANNOTATION_RUNTIME':str(RUNTIME)},stdout=log,stderr=log,stdin=subprocess.DEVNULL,start_new_session=True)
        info={'pid':proc.pid,'port':a.port,'start':Path(f'/proc/{proc.pid}/stat').read_text().split()[21]};pidfile.write_text(json.dumps(info))
        for _ in range(100):
            if proc.poll() is not None:print(f'Start failed; see {RUNTIME}/server.log');return 1
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{a.port}/api/videos',timeout=1) as response:count=len(json.load(response))
                print(f"Ready: http://127.0.0.1:{a.port} · {count} videos · PID {proc.pid}");print(f"Annotations: {RUNTIME}/annotations.sqlite3");return 0
            except (OSError,ValueError):time.sleep(.1)
        print(f'Starting slowly; see {RUNTIME}/server.log');return 1
if __name__=='__main__':raise SystemExit(main())
