import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.grading import GRADE_BANDS, describe_bands, grade_from_score, grade_label  # noqa: E402


class GradingTest(unittest.TestCase):
    def test_band_boundaries(self):
        cases = {
            0: "again",
            39: "again",
            40: "hard",
            64: "hard",
            65: "good",
            84: "good",
            85: "easy",
            100: "easy",
        }
        for score, expected in cases.items():
            with self.subTest(score=score):
                self.assertEqual(grade_from_score(score), expected)

    def test_score_is_rounded_like_the_ui(self):
        self.assertEqual(grade_from_score(84.6), "easy")
        self.assertEqual(grade_from_score(39.4), "again")

    def test_bands_are_contiguous_and_cover_0_to_100(self):
        ordered = sorted(GRADE_BANDS, key=lambda band: band.min_score)
        self.assertEqual(ordered[0].min_score, 0)
        self.assertEqual(ordered[-1].max_score, 100)
        for previous, current in zip(ordered, ordered[1:]):
            self.assertEqual(current.min_score, previous.max_score + 1)

    def test_invalid_scores_raise(self):
        for bad in (-1, 101, "80", None, True, float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    grade_from_score(bad)

    def test_labels(self):
        self.assertIn("Good", grade_label("good"))
        text = describe_bands()
        for token in ("Again", "Hard", "Good", "Easy"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
