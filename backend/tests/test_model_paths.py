"""Existing weights and external runtime stay usable after relocating the app."""
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

import models_local


class ModelPathsTests(unittest.TestCase):
    def test_old_and_new_manifests_resolve_to_the_same_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(models_local, 'MODELS', Path(temp)):
            for name in ('models/rtmw-l/pose.onnx', 'rtmw-l/pose.onnx'):
                self.assertEqual(models_local.pose_path({'pose': {'path': name}}), Path(temp) / 'rtmw-l/pose.onnx')

    def test_checkpoint_cannot_escape_model_directory(self):
        for name in ('../other/pose.onnx', '/tmp/other/pose.onnx'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                models_local.pose_path({'pose': {'path': name}})

    def test_perception_can_save_to_runtime_outside_code_directory(self):
        from perception import process_video
        with tempfile.TemporaryDirectory() as temp, patch.object(models_local, 'MODELS', Path(temp)):
            root = Path(temp)
            (root / 'manifest.json').write_text('{}')
            source = root / 'synthetic.mov'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                            'color=c=gray:s=64x96:r=4:d=1', '-c:v', 'libx264',
                            '-threads', '1', str(source)], check=True)
            output, evidence, metrics = process_video(source, None, output=root / 'external-runtime', fps=1)
            self.assertEqual(evidence['sampling']['coverage'], 'full_clip')
            self.assertEqual(metrics['sampled_frames'], 1)
            self.assertEqual(metrics['run'], str(output))
            self.assertTrue((output / 'frames/00000.jpg').is_file())
