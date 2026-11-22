import unittest

import numpy as np

from bapu.audio.synth import render_audio, score_to_wav
from bapu.music.model import NoteEvent, Score


class SynthTests(unittest.TestCase):
    def test_render_audio_length_and_level(self):
        score = Score(tempo=120, events=[NoteEvent(60, 0.0, 1.0)])
        audio = render_audio(score)
        self.assertEqual(len(audio), int((0.5 + 0.35) * 22050))
        self.assertGreater(float(np.max(np.abs(audio))), 0.1)
        self.assertLessEqual(float(np.max(np.abs(audio))), 1.0)

    def test_rendered_pitch_matches_score(self):
        score = Score(tempo=60, events=[NoteEvent(69, 0.0, 4.0, velocity=1.0)])
        audio = render_audio(score, waveform="sine")
        spectrum = np.abs(np.fft.rfft(audio[:int(3.0 * 22050)]))
        frequencies = np.fft.rfftfreq(int(3.0 * 22050), 1 / 22050)
        dominant = float(frequencies[int(np.argmax(spectrum))])
        self.assertLess(abs(dominant - 440.0), 2.0)

    def test_rest_is_silent(self):
        score = Score(tempo=120, events=[NoteEvent(None, 0.0, 2.0)])
        self.assertEqual(float(np.max(np.abs(render_audio(score)))), 0.0)

    def test_invalid_waveform(self):
        score = Score(events=[NoteEvent(60, 0.0, 1.0)])
        with self.assertRaises(ValueError):
            render_audio(score, waveform="invalid")

    def test_wav_output(self):
        score = Score(events=[NoteEvent(60, 0.0, 1.0)])
        wav = score_to_wav(score, waveform="piano")
        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")


if __name__ == "__main__":
    unittest.main()
