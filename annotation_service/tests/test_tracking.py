"""Exercise the configured tracker without loading detector weights."""
import unittest

import numpy as np
import supervision as sv

from annotation_service.preannotation.detector import Detector


class TrackingTests(unittest.TestCase):
    def test_identity_survives_brief_detection_gap_but_expires(self):
        detector = Detector.__new__(Detector)
        detector.sv = sv
        detector.reset()
        tracker = detector.trackers['person']

        def present():
            return tracker.update_with_detections(sv.Detections(
                xyxy=np.array([[10., 10., 40., 60.]]), confidence=np.array([.9])))

        original = present().tracker_id.tolist()
        present()
        for _ in range(3):  # 1.5 s at the preannotation sampling rate
            tracker.update_with_detections(sv.Detections.empty())
        self.assertEqual(present().tracker_id.tolist(), original)
        for _ in range(12):  # 6 s: do not keep identity indefinitely
            tracker.update_with_detections(sv.Detections.empty())
        present()
        self.assertNotEqual(present().tracker_id.tolist(), original)


if __name__ == '__main__':
    unittest.main()
