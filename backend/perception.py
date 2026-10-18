"""Video -> measured evidence. Inputs and existing labels are read-only."""
import hashlib
import json
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
from PIL import Image

from evidence import derive_signals, wrist_relations

ROOT = Path(__file__).resolve().parent


def draw(frame, detections, poses, timestamp):
    canvas = frame.copy()
    for item in detections:
        x1, y1, x2, y2 = map(int, item["box_xyxy"])
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 220, 255), 2)
        text = f'{item["label"]} {item["score"]:.2f} {item["track_id"] or "?"}'
        cv2.putText(canvas, text, (max(0, x1), max(18, y1)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 220, 255), 1)
    for pose in poses:
        for index, (x, y, confidence) in enumerate(pose["keypoints"]):
            if confidence >= 0.45 and (index < 17 or index >= 91):
                cv2.circle(canvas, (round(x), round(y)), 2, (100, 255, 50), -1)
    cv2.putText(canvas, f"{timestamp:.2f}s | sampled preview", (8, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return canvas


def process_video(source, models, *, output=None, fps=2, max_frames=0, threshold=0.22,
                  pose_device="cpu", tiles=False, deadline=None, check_cancelled=None):
    from models_local import CLASSES, ClassTracker, MODELS
    started = time.monotonic()
    if models is not None:
        models.tracker = ClassTracker(fps)  # Track IDs never carry over to another video.
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError("Cannot decode input video")
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    original_fps = cap.get(cv2.CAP_PROP_FPS)
    if original_fps <= 0:
        raise RuntimeError("Invalid source frame rate")
    duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / original_fps
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output = ROOT / "runs" / f"{source.stem}_{stamp}"
    (output / "frames").mkdir(parents=True)
    (output / "crops").mkdir()
    preview = cv2.VideoWriter(str(output / "preview.avi"), cv2.VideoWriter_fourcc(*"MJPG"),
                              fps, (width, height))
    if not preview.isOpened():
        raise RuntimeError("Cannot create preview video")
    frames, timings = [], []
    frame_index, next_sample, exhausted = 0, 0.0, False
    try:
        while True:
            if check_cancelled is not None:check_cancelled()
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Perception time budget exceeded")
            ok, image = cap.read()
            if not ok:
                exhausted = True
                break
            timestamp = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if timestamp <= 0 and frame_index:
                timestamp = frame_index / original_fps
            frame_index += 1
            if timestamp + 1e-5 < next_sample:
                continue
            next_sample = (int(timestamp * fps + 1e-5) + 1) / fps
            tick = time.monotonic()
            rgb = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
            detections = models.detect(rgb) if models is not None else []
            detected_at = time.monotonic()
            poses = models.estimate_pose(image, detections) if models is not None else []
            posed_at = time.monotonic()
            if any(len(p["keypoints"]) != 133 for p in poses):
                raise RuntimeError("Expected RTMW whole-body output with 133 keypoints")
            relations = [relation for pose in poses
                         for relation in wrist_relations(pose, detections, width, height)]
            frame_path = f"frames/{len(frames):05d}.jpg"
            rgb.save(output / frame_path, quality=90)
            crops = []
            for number, item in enumerate(detections):
                if item["label"] == "person" or item["score"] < 0.3:
                    continue
                box = item["box_xyxy"]
                x1, y1 = max(0, int(box[0])-10), max(0, int(box[1])-10)
                x2, y2 = min(width, int(box[2])+10), min(height, int(box[3])+10)
                if x2 <= x1 or y2 <= y1:
                    continue
                path = f"crops/{len(frames):05d}_{number}.jpg"
                rgb.crop((x1, y1, x2, y2)).save(output / path, quality=90)
                crops.append({"path": path, "candidate_label": item["label"],
                              "score": item["score"], "box_xyxy": [x1, y1, x2, y2]})
            frames.append({"time_sec": round(timestamp, 4), "image": frame_path,
                           "detections": detections, "poses": poses,
                           "relations": relations, "crops": crops})
            preview.write(draw(image, detections, poses, timestamp))
            timings.append({"detector_tracker_sec": detected_at-tick,
                            "pose_sec": posed_at-detected_at})
            if max_frames and len(frames) >= max_frames:
                break
    finally:
        cap.release()
        preview.release()
    if not frames:
        raise RuntimeError("No sampled frames")
    result = {"schema_version": 1, "source": {"path": str(source),
              "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "duration_sec": duration, "width": width, "height": height},
              "sampling": {"requested_fps": fps, "count": len(frames),
                           "coverage": "full_clip" if exhausted else "prefix_only",
                           "last_sample_sec": frames[-1]["time_sec"]},
              "models_manifest_sha256": hashlib.sha256((MODELS / "manifest.json").read_bytes()).hexdigest(),
              "configuration": {"threshold": threshold, "pose_device": pose_device,
                                "classes": CLASSES, "tiles": tiles},
              "frames": frames, "signals": derive_signals(frames),
              "limitations": ["Detections and keypoints are uncertain model predictions.",
                              "Missed detections do not prove absence or departure.",
                              "Wrist proximity does not prove contact, transfer or locking.",
                              "No automatically assigned cashier/customer roles or document identity.",
                              "Sparse sampling can miss short actions; no violation is decided by perception."],
              "timings": {"total_sec": time.monotonic()-started, "per_frame": timings}}
    (output / "evidence.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(output / "preview.avi"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    "-threads", "2", str(output / "preview.mp4")], check=True)
    (output / "preview.avi").unlink()
    metrics = {"run": str(output.relative_to(ROOT) if output.is_relative_to(ROOT) else output), "sampled_frames": len(frames),
               "detections": dict(Counter(d["label"] for f in frames for d in f["detections"])),
               "poses": sum(len(f["poses"]) for f in frames),
               "tracked_detections": sum(d["track_id"] is not None for f in frames for d in f["detections"]),
               "signals": len(result["signals"]), "runtime_sec": result["timings"]["total_sec"]}
    (output / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2)+"\n")
    return output, result, metrics
