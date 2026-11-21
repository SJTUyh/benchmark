import random
import unittest

from ais_bench.benchmark.utils.online_badcase import (
    DEFAULT_CONFIG,
    LONG_SEGMENT,
    SHORT_CYCLE,
    OnlineBadcaseDetector,
)


class TestOnlineBadcaseDetector(unittest.TestCase):
    def test_short_cycle_hit(self):
        detector = OnlineBadcaseDetector(
            {
                "min_chars": 50,
                "check_interval_chars": 1,
                "window_chars": 100,
                "min_period": 3,
                "max_period": 3,
                "min_repeats": 5,
                "min_repeated_chars": 15,
                "long_segment_chars": 20,
            }
        )
        self.assertIsNone(detector.feed("".join(chr(0x4E00 + i) for i in range(60))))
        result = detector.feed("abc" * 20)

        self.assertIsNotNone(result)
        self.assertEqual(result["reason"], SHORT_CYCLE)
        self.assertEqual(result["period"], 3)
        self.assertEqual(result["total_chars"], 120)
        self.assertGreaterEqual(result["repeat_chars"], 15)
        self.assertFalse(result["aborted"])
        self.assertFalse(result["max_out_len_hit"])

    def test_long_segment_hit(self):
        segment = "S" * 30
        detector = OnlineBadcaseDetector(
            {
                "min_chars": 50,
                "check_interval_chars": 1,
                "window_chars": 100,
                "min_period": 20,
                "max_period": 20,
                "min_repeats": 10,
                "min_repeated_chars": 200,
                "long_segment_chars": 30,
            }
        )
        result = detector.feed("y" * 40 + segment + segment)

        self.assertIsNotNone(result)
        self.assertEqual(result["reason"], LONG_SEGMENT)
        self.assertIsNone(result["period"])
        self.assertEqual(result["repeat_chars"], 60)

    def test_normal_long_text_no_hit(self):
        detector = OnlineBadcaseDetector(DEFAULT_CONFIG)
        rng = random.Random(1234)
        text = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(1000))
        self.assertIsNone(detector.feed(text))
        self.assertIsNone(detector.finish(output_tokens=1000))

    def test_min_chars_protection(self):
        detector = OnlineBadcaseDetector(DEFAULT_CONFIG)
        self.assertIsNone(detector.feed("abc" * 10))
        self.assertIsNone(detector.finish(output_tokens=30))

    def test_feed_returns_once(self):
        detector = OnlineBadcaseDetector(
            {
                "min_chars": 10,
                "check_interval_chars": 1,
                "window_chars": 50,
                "min_period": 2,
                "max_period": 2,
                "min_repeats": 5,
                "min_repeated_chars": 10,
                "long_segment_chars": 20,
            }
        )
        self.assertIsNotNone(detector.feed("ab" * 6))
        self.assertIsNone(detector.feed("ab" * 6))
        self.assertTrue(detector.hit)

    def test_finish_marks_max_out_len_hit(self):
        detector = OnlineBadcaseDetector(
            {
                "min_chars": 10,
                "check_interval_chars": 1,
                "window_chars": 50,
                "min_period": 2,
                "max_period": 2,
                "min_repeats": 5,
                "min_repeated_chars": 10,
                "long_segment_chars": 20,
            },
            max_out_len=20,
        )
        self.assertIsNotNone(detector.feed("ab" * 6))

        result = detector.finish(output_tokens=20)

        self.assertIsNotNone(result)
        self.assertTrue(result["max_out_len_hit"])
        self.assertEqual(result["total_chars"], 12)

    def test_abort_flag_is_configurable(self):
        self.assertTrue(OnlineBadcaseDetector(DEFAULT_CONFIG).abort_enabled)
        detector = OnlineBadcaseDetector({"abort": False})
        self.assertFalse(detector.abort_enabled)


if __name__ == "__main__":
    unittest.main()
