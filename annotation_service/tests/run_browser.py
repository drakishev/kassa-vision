"""Isolated browser integration test; never serves the real dataset."""
import argparse,os,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
from .test_service import make_fixture,annotation
from annotation_service.drafts import DraftStore
from annotation_service.preannotation.common import video_id

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--playwright-module',required=True);p.add_argument('--chromium',required=True);p.add_argument('--axe-module');p.add_argument('--output')
    p.add_argument('--integrated',action='store_true',help='Exercise the authenticated /annotate mount')
    args=p.parse_args();root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='cash-annotation-test-') as temp:
        tmp=Path(temp);dataset=make_fixture(tmp/'dataset');out=Path(args.output or tmp/'screenshots');out.mkdir(parents=True,exist_ok=True)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env={**os.environ,'ANNOTATION_DATASET':str(dataset),'ANNOTATION_RUNTIME':str(tmp/'runtime')}
        origin=f'http://127.0.0.1:{port}';url=origin
        target='annotation_service.app:create_app'
        if args.integrated:
            sys.path.insert(0,str(root/'backend'))
            from service.accounts import AccountStore
            AccountStore(tmp/'api/ui/sessions.sqlite3').create_user('tester','synthetic-password-42')
            env.update(KASSA_RUNTIME=str(tmp/'api'),PYTHONPATH=str(root)+os.pathsep+str(root/'backend'))
            target='service.ui_gateway:create_ui';url=origin+'/annotate'
        with (tmp/'server.log').open('wb') as log:
            server=subprocess.Popen([sys.executable,'-m','uvicorn',target,'--factory','--host','127.0.0.1','--port',str(port)],cwd=root,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if server.poll() is not None:raise RuntimeError((tmp/'server.log').read_text())
                try:
                    with urllib.request.urlopen(origin+('/ui/api/auth/me' if args.integrated else '/api/config'),timeout=1) as r:r.read()
                    break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Test server startup timed out')
            draft=annotation();draft['objects'][0]['keyframes'][0]['polygons']=[[[.1,.1],[.3,.1],[.3,.4],[.1,.4]]];draft['objects'][0]['keyframes'][0]['mask_source']='sam2.1_small'
            DraftStore(tmp/'runtime/annotations.sqlite3').publish(video_id('demo-2.mp4'),{'run_id':'synthetic_browser','status':'done','annotation':draft,'metadata':{},'coverage':{'windows':[{'start':0,'end':40}]}})
            axe=Path(args.axe_module) if args.axe_module else Path(args.playwright_module).parent/'@axe-core/playwright'
            browser_env={**os.environ,'PLAYWRIGHT_MODULE':str(Path(args.playwright_module).resolve()),'CHROMIUM_EXECUTABLE':str(Path(args.chromium).resolve()),'ANNOTATION_TEST_URL':url,'ANNOTATION_TEST_OUTPUT':str(out.resolve())}
            if axe.exists():browser_env['AXE_MODULE']=str(axe.resolve())
            subprocess.run(['node',str(Path(__file__).with_name('browser.cjs'))],env=browser_env,cwd=root,check=True,timeout=180)
        finally:
            server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait()
if __name__=='__main__':main()
