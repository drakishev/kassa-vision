import unittest

from analyze import evenly_spaced, parse_output


class AnalyzeTests(unittest.TestCase):
    def test_sampling_preserves_first_and_last_without_labels(self):
        self.assertEqual(evenly_spaced(list(range(20)), 3), [0, 10, 19])

    def test_accepts_fenced_json_from_local_model(self):
        data = parse_output('```json\n{"verdict":"no_violation","events":[],"uncertainties":[]}\n```')
        self.assertEqual(data["events"], [])

    def test_truncated_output_is_not_a_successful_result(self):
        with self.assertRaises(ValueError):
            parse_output('{"verdict":"violation","events":[')

    def test_unknown_verdict_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_output('{"verdict":"unknown","events":[],"uncertainties":[]}')


if __name__ == "__main__":
    unittest.main()
