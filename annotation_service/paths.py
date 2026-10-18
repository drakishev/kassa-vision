"""Portable dataset and runtime locations shared by the UI and optional workers."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dataset_path(root=ROOT):
    return Path(os.environ.get('ANNOTATION_DATASET', root / 'data/dataset')).resolve()


def runtime_path(root=ROOT):
    return Path(os.environ.get('ANNOTATION_RUNTIME', root / 'data/annotation_service')).resolve()


def source_records(root=ROOT):
    dataset = dataset_path(root)
    labels = dataset / 'reference/source_labels.json'
    if labels.exists():
        return json.loads(labels.read_text())
    return [{'filename': p.name} for p in sorted((dataset / 'open/videos').glob('*'))
            if p.is_file() and p.suffix.lower() in {'.mov', '.mp4', '.mkv', '.avi'}]
