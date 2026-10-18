import unittest

from service.launch import choose_gpu


class GPUTests(unittest.TestCase):
    def test_auto_selects_gpu_with_enough_free_memory(self):
        self.assertEqual(choose_gpu("auto", "0, 1000\n1, 100000\n2, 130000\n"), "2")

    def test_explicit_busy_gpu_fails_before_model_loading(self):
        with self.assertRaises(ValueError):
            choose_gpu("1", "0, 130000\n1, 1000\n")

    def test_auto_fails_when_no_gpu_has_room(self):
        with self.assertRaises(ValueError):
            choose_gpu("auto", "0, 2000\n1, 1000\n")


if __name__ == "__main__":
    unittest.main()
