import unittest

from models_local import ClassTracker


def box(label):
    return {"label": label, "score": 0.9, "box_xyxy": [10, 10, 80, 100]}


class TrackingTests(unittest.TestCase):
    def test_overlapping_different_classes_have_distinct_track_ids(self):
        result = ClassTracker(2).update([box("person"), box("bag")])
        self.assertNotEqual(result[0]["track_id"], result[1]["track_id"])
        self.assertIsNotNone(result[0]["track_id"])

    def test_short_detection_gap_preserves_track(self):
        tracker = ClassTracker(2)
        first = tracker.update([box("person")])[0]["track_id"]
        self.assertEqual(tracker.update([]), [])
        last = tracker.update([box("person")])[0]["track_id"]
        self.assertEqual(first, last)


if __name__ == "__main__":
    unittest.main()
