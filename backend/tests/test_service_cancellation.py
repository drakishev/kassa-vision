import unittest
from service.cancellation import AnalysisCancelled, CancellationCriteria

class CancellationTests(unittest.TestCase):
    def test_generation_checkpoint_calls_guard_and_interrupts_immediately(self):
        cancelled=False
        def guard():
            if cancelled:raise AnalysisCancelled()
        criterion=CancellationCriteria(guard)
        self.assertFalse(criterion(None,None))
        cancelled=True
        with self.assertRaises(AnalysisCancelled):criterion(None,None)

    def test_video_decoding_stops_at_cancel_checkpoint(self):
        import subprocess,tempfile
        from pathlib import Path
        from perception import process_video
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'synthetic.mov'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=gray:s=64x96:r=30:d=1','-c:v','libx264','-threads','1',str(source)],check=True)
            checks=[]
            def guard():
                checks.append(True)
                if len(checks)==2:raise AnalysisCancelled()
            with self.assertRaises(AnalysisCancelled):
                process_video(source,None,output=root/'frames',check_cancelled=guard)
            self.assertEqual(len(checks),2)
