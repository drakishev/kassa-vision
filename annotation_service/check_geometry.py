"""Read-only report of saved polygons that the current validator would reject.

Rows accepted before geometry validation existed may contain self-intersecting
or degenerate masks/zones. This never modifies the database.

    python -m annotation_service.check_geometry [path/to/annotations.sqlite3]
"""
import json, sqlite3, sys
from pathlib import Path
from .paths import runtime_path
from .geometry import area, crossing

ROOT = Path(__file__).resolve().parents[1]


def invalid(annotation):
    for zone in annotation.get('zones', []):
        if area(zone['points']) < 1e-5 or crossing(zone['points']):
            yield {'kind': 'zone', 'id': zone['id']}
    for obj in annotation.get('objects', []):
        for keyframe in obj['keyframes']:
            for index, polygon in enumerate(keyframe.get('polygons', [])):
                if area(polygon) < 1e-8 or crossing(polygon):
                    yield {'kind': 'mask', 'id': obj['id'], 'time': keyframe['time'], 'polygon': index}


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else runtime_path()/'annotations.sqlite3').resolve()
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        rows = db.execute('SELECT video_id, revision, body FROM annotations').fetchall()
    report = [{'video_id': vid, 'revision': rev, **item} for vid, rev, body in rows for item in invalid(json.loads(body))]
    print(json.dumps({'videos_checked': len(rows), 'invalid': report}, ensure_ascii=False, indent=2))
    return 1 if report else 0


if __name__ == '__main__':
    raise SystemExit(main())
