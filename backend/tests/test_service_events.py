import unittest

from service.events import merge_events, window_bounds
from service.schemas import DoneJob


class EventTests(unittest.TestCase):
    def test_windows_cover_end_of_long_clip(self):
        bounds = window_bounds(145, 12, 2)
        self.assertEqual(bounds[0][0], 0)
        self.assertEqual(bounds[-1][1], 145)
        self.assertTrue(all(a[1] >= b[0] for a, b in zip(bounds, bounds[1:])))

    def test_tiny_clip_has_one_window(self):
        self.assertEqual(window_bounds(1, 12, 2), [(0, 1)])

    def test_duplicate_overlap_event_is_merged(self):
        event = {"type": "V05", "time_sec": 10, "visible_evidence": "Деньги на столе"}
        other = {**event, "time_sec": 11}
        self.assertEqual(len(merge_events([event, other])), 1)

    def test_different_events_or_distant_times_remain_separate(self):
        events = [{"type": "V05", "time_sec": 10, "visible_evidence": "Деньги"},
                  {"type": "safe_key", "time_sec": 10, "visible_evidence": "Ключ"},
                  {"type": "V05", "time_sec": 50, "visible_evidence": "Деньги"}]
        self.assertEqual(len(merge_events(events)), 3)

    def test_no_violation_cannot_contain_events(self):
        with self.assertRaises(ValueError):
            DoneJob(job_id="a", verdict="violation", violations=[])

    def test_empty_window_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            window_bounds(10, 2, 2)


if __name__ == "__main__":
    unittest.main()
