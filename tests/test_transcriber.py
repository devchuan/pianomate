import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from bapu.audio.transcriber import SR, transcribe


def render(midis, beat_seconds=0.5):
    chunks = []
    for midi in midis:
        t = np.arange(int(SR * beat_seconds)) / SR
        envelope = np.clip(np.minimum(1.0, t / 0.012) *
                           np.minimum(1.0, (beat_seconds - t) / 0.04), 0, 1)
        frequency = 440.0 * 2 ** ((midi - 69) / 12)
        chunks.append(np.sin(2 * np.pi * frequency * t) * envelope * 0.6)
    return np.concatenate(chunks)


class TranscriberTests(unittest.TestCase):
    def test_pitch_and_rhythm(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "melody.wav"
            sf.write(path, render([60, 64, 67, 72]), SR)
            score = transcribe(path, tempo=120.0, key="C")
        self.assertEqual([e.midi for e in score.events], [60, 64, 67, 72])
        self.assertEqual([e.beats for e in score.events], [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(score.tempo, 120.0)
        self.assertEqual(score.tonic, "C")

    def test_trailing_rest_padding(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "short.wav"
            sf.write(path, render([60, 64]), SR)
            score = transcribe(path, tempo=120.0, key="C")
        self.assertEqual(sum(event.beats for event in score.events), 4.0)
        last = score.events[-1]
        self.assertIsNone(last.midi)
        self.assertEqual(last.start_beat, 2.0)
        self.assertEqual(last.beats, 2.0)

    def test_auto_tempo(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "melody.wav"
            sf.write(path, render([60, 62, 64, 65, 67, 69, 71, 72]), SR)
            score = transcribe(path, key="C")
        self.assertGreaterEqual(score.tempo, 40)
        self.assertLessEqual(score.tempo, 240)


if __name__ == "__main__":
    unittest.main()
