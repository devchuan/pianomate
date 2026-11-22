import unittest
from unittest.mock import patch

from bapu import omr
from bapu.music import score_to_musicxml
from bapu.music.model import NoteEvent, Score

SAMPLE = score_to_musicxml(Score(
    title="OMR Test", piano=True,
    events=[NoteEvent(72, 0, 2), NoteEvent(48, 0, 2, staff=1)],
))


class OMRTests(unittest.TestCase):
    def test_fallback_to_audiveris(self):
        with patch.object(omr, "_run_oemer", side_effect=RuntimeError("oemer failed")), \
             patch.object(omr, "_run_audiveris", return_value=SAMPLE) as audiveris:
            result, engine = omr.convert_image(b"image", "score.png")
        self.assertEqual(engine, "audiveris")
        self.assertEqual(result, SAMPLE)
        audiveris.assert_called_once()

    def test_reject_unsupported_suffix(self):
        with self.assertRaises(RuntimeError):
            omr.convert_image(b"data", "score.txt")


if __name__ == "__main__":
    unittest.main()
