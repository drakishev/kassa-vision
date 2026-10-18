"""Download public weights only; never read or upload private dataset content."""
import hashlib
import json
import os
from pathlib import Path
from zipfile import ZipFile

import requests
from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parent
POSE_URL = ("https://download.openmmlab.com/mmpose/v1/projects/rtmw/onnx_sdk/"
            "rtmw-dw-x-l_simcc-cocktail14_270e-384x288_20231122.zip")


def main():
    models = Path(os.environ.get("KASSA_MODELS_DIR", ROOT / "models")).resolve()
    models.mkdir(parents=True, exist_ok=True)
    repo = "IDEA-Research/grounding-dino-base"
    revision = '12bdfa3120f3e7ec7b434d90674b3396eccf88eb'  # The deployed detector revision.
    snapshot_download(repo, revision=revision, local_dir=models / "grounding-dino-base",
                      allow_patterns=["*.json", "*.safetensors", "*.txt", "LICENSE*"])
    archive = models / "rtmw-l-384x288.zip"
    if not archive.exists():
        temporary = archive.with_suffix(".partial")
        with requests.get(POSE_URL, stream=True, timeout=(15, 120)) as response:
            response.raise_for_status()
            with temporary.open("wb") as stream:
                for chunk in response.iter_content(1024 * 1024):
                    stream.write(chunk)
        temporary.replace(archive)
    pose_dir = models / "rtmw-l"
    pose_dir.mkdir(exist_ok=True)
    with ZipFile(archive) as bundle:
        for name in bundle.namelist():
            # Extract the ONNX file by basename, avoiding arbitrary archive paths.
            if name.endswith(".onnx"):
                (pose_dir / Path(name).name).write_bytes(bundle.read(name))
    weights = list(pose_dir.glob("*.onnx"))
    if len(weights) != 1:
        raise RuntimeError(f"Expected one RTMW ONNX checkpoint, got {len(weights)}")
    with weights[0].open("rb") as stream:
        pose_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if pose_digest != 'bd033156e5104c4f5d2edfe0453e02661e30a2f3da453ec93c8764d561b83054':
        raise RuntimeError("Downloaded pose weights differ from the tested checkpoint")
    files = {}
    for file in models.rglob("*"):
        if file.is_file() and ".cache" not in file.parts and file.suffix != ".zip":
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files[str(file.relative_to(models))] = digest
    manifest = {"detector": {"repo": repo, "revision": revision,
                             "license_model_card": "Apache-2.0"},
                "pose": {"name": "RTMW-l", "input_wh": [288, 384],
                         "source_url": POSE_URL, "path": str(weights[0].relative_to(models)),
                         "source_project": "https://github.com/open-mmlab/mmpose"},
                "tracker": {"algorithm": "ByteTrack", "implementation": "supervision 0.25.1"},
                "weights_sha256": files}
    (models / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": "prepared", "weight_files_hashed": len(files)}))


if __name__ == "__main__":
    main()
