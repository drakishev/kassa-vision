"""Local detector, class-aware ByteTrack, and whole-body pose adapters."""
import json
import os
from pathlib import Path

import numpy as np
import supervision as sv
from evidence import remap_box, suppress_duplicates

ROOT = Path(__file__).resolve().parent
MODELS = Path(os.environ.get("KASSA_MODELS_DIR", ROOT / "models")).resolve()


def pose_path(manifest):
    path = Path(manifest["pose"]["path"])
    # Read old manifests without moving existing weights.
    if path.parts and path.parts[0] == "models":
        path = Path(*path.parts[1:])
    resolved = (MODELS / path).resolve()
    if not resolved.is_relative_to(MODELS):
        raise ValueError("Pose checkpoint must be inside KASSA_MODELS_DIR")
    return resolved


CLASSES = ["person", "banknotes", "document", "bag", "safe", "cup"]


class ClassTracker:
    def __init__(self, fps):
        self.trackers = {label: sv.ByteTrack(track_activation_threshold=0.20,
                                           lost_track_buffer=60,
                                           frame_rate=max(1, round(fps))) for label in CLASSES}

    def update(self, detections):
        for item in detections:
            item["track_id"] = None
        for label, tracker in self.trackers.items():
            indices = [i for i, item in enumerate(detections) if item["label"] == label]
            if indices:
                data = sv.Detections(
                    xyxy=np.asarray([detections[i]["box_xyxy"] for i in indices], dtype=float),
                    confidence=np.asarray([detections[i]["score"] for i in indices]),
                    class_id=np.full(len(indices), CLASSES.index(label)),
                    data={"source_index": np.asarray(indices)})
            else:
                data = sv.Detections.empty()
            tracked = tracker.update_with_detections(data)
            for index, track_id in zip(tracked.data.get("source_index", []), tracked.tracker_id):
                detections[int(index)]["track_id"] = f"{label}:{int(track_id)}"
        return detections


class PerceptionModels:
    def __init__(self, fps, threshold, pose_device, tiles=False):
        import torch
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        from rtmlib import RTMPose

        torch.set_num_threads(8)
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.threshold = threshold
        self.tiles = tiles
        folder = MODELS / "grounding-dino-base"
        self.processor = AutoProcessor.from_pretrained(folder, local_files_only=True)
        self.detector = AutoModelForZeroShotObjectDetection.from_pretrained(
            folder, local_files_only=True).to(self.device).eval()
        manifest = json.loads((MODELS / "manifest.json").read_text())
        self.pose = RTMPose(str(pose_path(manifest)),
                           model_input_size=(288, 384), backend="onnxruntime",
                           device=pose_device)
        if pose_device == "cpu":
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 8
            options.inter_op_num_threads = 1
            options.log_severity_level = 3
            self.pose.session = ort.InferenceSession(str(pose_path(manifest)),
                    sess_options=options, providers=["CPUExecutionProvider"])
        self.tracker = ClassTracker(fps)

    def detect_one(self, image):
        prompt = ". ".join(CLASSES) + "."
        inputs = self.processor(images=image, text=prompt, return_tensors="pt").to(self.device)
        with self.torch.inference_mode():
            outputs = self.detector(**inputs)
        result = self.processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=self.threshold, text_threshold=0.25,
            target_sizes=[(image.height, image.width)])[0]
        detections = []
        for box, score, label in zip(result["boxes"], result["scores"], result["text_labels"]):
            # Reject ambiguous phrases rather than selecting an arbitrary category.
            if label.strip() not in CLASSES:
                continue
            detections.append({"label": label.strip(), "score": round(float(score), 4),
                               "box_xyxy": [round(float(x), 2) for x in box]})
        return detections

    def detect(self, image):
        detections = self.detect_one(image)
        if self.tiles:
            # Overlapping crops expose small objects at larger effective scale.
            w, h = image.size
            for x1, y1, x2, y2 in [(0, 0, 0.65*w, 0.65*h),
                                   (0.35*w, 0, w, 0.65*h),
                                   (0, 0.35*h, 0.65*w, h),
                                   (0.35*w, 0.35*h, w, h)]:
                region = tuple(map(int, (x1, y1, x2, y2)))
                for item in self.detect_one(image.crop(region)):
                    if item["label"] == "person":
                        continue  # Partial-person crops destabilize whole-person tracking.
                    item["box_xyxy"] = remap_box(item["box_xyxy"], region[:2])
                    detections.append(item)
        return self.tracker.update(suppress_duplicates(detections))

    def estimate_pose(self, bgr_image, detections):
        people = sorted((d for d in detections if d["label"] == "person"),
                        key=lambda d: d["score"], reverse=True)[:4]
        if not people:
            return []
        keypoints, scores = self.pose(bgr_image, bboxes=[p["box_xyxy"] for p in people])
        return [{"track_id": person["track_id"], "keypoints":
                 [[round(float(x), 2), round(float(y), 2), round(float(s), 4)]
                  for (x, y), s in zip(points, confidence)]}
                for person, points, confidence in zip(people, keypoints, scores)]
