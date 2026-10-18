"""Measured candidates only: no legal decisions, identities, or inferred intent."""
import math
from collections import defaultdict


def remap_box(box, origin):
    return [box[0]+origin[0], box[1]+origin[1], box[2]+origin[0], box[3]+origin[1]]


def suppress_duplicates(detections, threshold=0.5):
    kept = []
    for item in sorted(detections, key=lambda d: d["score"], reverse=True):
        a = item["box_xyxy"]
        duplicate = False
        for other in kept:
            if item["label"] != other["label"]:
                continue
            b = other["box_xyxy"]
            intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
            area_a = max(0, a[2]-a[0])*max(0, a[3]-a[1])
            area_b = max(0, b[2]-b[0])*max(0, b[3]-b[1])
            if intersection / max(area_a+area_b-intersection, 1e-6) > threshold:
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
    return kept


def wrist_relations(pose, detections, width, height, threshold=0.45):
    relations = []
    for index, side in [(9, "left"), (10, "right")]:
        x, y, confidence = pose["keypoints"][index]
        if confidence < threshold:
            continue
        for item in detections:
            if item["label"] == "person":
                continue
            x1, y1, x2, y2 = item["box_xyxy"]
            distance = math.hypot(max(x1-x, 0, x-x2), max(y1-y, 0, y-y2))
            if distance <= 0.025 * math.hypot(width, height):
                relations.append({"type": "wrist_near_box", "side": side,
                                  "person_track_id": pose["track_id"],
                                  "object_label": item["label"],
                                  "object_track_id": item["track_id"],
                                  "distance_pixels": round(distance, 2),
                                  "contact_confirmed": False})
    return relations


def derive_signals(frames):
    tracks = defaultdict(list)
    for frame in frames:
        for detection in frame["detections"]:
            if detection["track_id"] is not None:
                key = (detection["label"], detection["track_id"])
                tracks[key].append((frame["time_sec"], detection))
    signals = []
    last_time = frames[-1]["time_sec"] if frames else 0
    for (label, track_id), observations in tracks.items():
        first, last = observations[0][0], observations[-1][0]
        if label == "person" and last < last_time:
            signals.append({"type": "track_detection_ended", "track_id": track_id,
                            "label": label, "last_detection_sec": last,
                            "occlusion_or_missed_detection_possible": True})
        if label == "person" or len(observations) < 3 or last-first < 2:
            continue
        boxes = [d["box_xyxy"] for _, d in observations]
        centers = [((b[0]+b[2])/2, (b[1]+b[3])/2) for b in boxes]
        diagonal = max(math.hypot(boxes[0][2]-boxes[0][0], boxes[0][3]-boxes[0][1]), 1)
        displacement = max(math.dist(centers[0], c) for c in centers)
        if displacement <= 0.35 * diagonal:
            signals.append({"type": "stationary_object_candidate", "label": label,
                            "track_id": track_id, "start_sec": first, "end_sec": last,
                            "sample_count": len(observations),
                            "interpretation": "Repeated boxes; object identity and unattended status unconfirmed"})
    return signals
