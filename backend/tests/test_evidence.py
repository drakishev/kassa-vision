import unittest

from evidence import derive_signals, wrist_relations


def frame(t, detections):
    return {"time_sec": t, "detections": detections}


def detection(label, track_id=1):
    return {"label": label, "track_id": track_id, "score": 0.8,
            "box_xyxy": [10, 10, 30, 30]}


class EvidenceTests(unittest.TestCase):
    def test_empty_frames_do_not_claim_departure_or_violation(self):
        self.assertEqual(derive_signals([frame(0, []), frame(1, [])]), [])

    def test_gap_does_not_mean_person_left(self):
        signals = derive_signals([frame(0, [detection("person")]), frame(1, [])])
        self.assertEqual(signals[0]["type"], "track_detection_ended")
        self.assertTrue(signals[0]["occlusion_or_missed_detection_possible"])
        self.assertNotIn("violation", signals[0])

    def test_untracked_objects_are_not_merged_into_one_track(self):
        frames = [frame(t, [detection("banknotes", None)]) for t in [0, 1, 2]]
        self.assertEqual(derive_signals(frames), [])

    def test_stationary_candidate_requires_time_and_repeated_detections(self):
        frames = [frame(t, [detection("banknotes")]) for t in [0, 1, 3]]
        signals = derive_signals(frames)
        self.assertEqual(signals[0]["type"], "stationary_object_candidate")
        self.assertEqual(signals[0]["label"], "banknotes")

    def test_low_confidence_wrist_is_not_evidence(self):
        pose = {"track_id": 2, "keypoints": [[0, 0, 0.0]] * 133}
        self.assertEqual(wrist_relations(pose, [detection("bag")], 100, 100), [])

    def test_nearby_wrist_is_proximity_not_contact(self):
        points = [[0, 0, 0.0] for _ in range(133)]
        points[9] = [20, 20, 0.9]
        pose = {"track_id": 2, "keypoints": points}
        relations = wrist_relations(pose, [detection("bag")], 100, 100)
        self.assertEqual(relations[0]["type"], "wrist_near_box")
        self.assertFalse(relations[0]["contact_confirmed"])


if __name__ == "__main__":
    unittest.main()
