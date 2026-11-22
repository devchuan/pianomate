import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from bapu.audio import transcribe
from bapu.audio.transcriber import SR
from bapu.music import (
    jianpu_to_score, musicxml_to_score, score_to_jianpu, score_to_musicxml, score_to_pdf,
)
from bapu.music.model import NoteEvent, Score
from bapu.music.pdf import _draw_jianpu_section, _groups


def piano_audio():
    progression = [[48, 52, 55], [50, 53, 57], [43, 47, 50], [48, 52, 55]]
    chunks = []
    for chord in progression:
        beat = 0.5
        t = np.arange(int(SR * beat)) / SR
        envelope = np.clip(np.minimum(1.0, t / 0.012) *
                           np.minimum(1.0, (beat - t) / 0.04), 0, 1)
        tones = [72] + chord
        chunks.append(sum(np.sin(2 * np.pi * 440 * 2 ** ((n - 69) / 12) * t)
                          for n in tones) * envelope * 0.25 / len(tones))
    return np.concatenate(chunks)


class PianoTests(unittest.TestCase):
    def test_chords_and_hand_split(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "piano.wav"
            sf.write(path, piano_audio(), SR)
            score = transcribe(path, tempo=120.0, key="C", piano=True)
        self.assertTrue(score.piano)
        bass = [event for event in score.events if event.staff == 1]
        treble = [event for event in score.events if event.staff == 0]
        self.assertTrue(bass)
        self.assertTrue(treble)
        self.assertTrue(all(event.midi is not None and event.midi < 60
                            for event in bass if event.midi is not None))
        self.assertTrue(all(event.midi is None or event.midi >= 60
                            for event in treble))
        first_chord = sorted({event.midi for event in bass if event.start_beat < 1
                              and event.midi is not None})
        self.assertTrue(set([48, 52, 55]).issubset(set(first_chord)))

    def test_grand_staff_musicxml_roundtrip(self):
        score = Score(title="钢琴", piano=True, events=[
            NoteEvent(72, 0, 1, staff=0), NoteEvent(60, 0, 1, staff=1),
            NoteEvent(64, 0, 1, staff=1), NoteEvent(67, 0, 1, staff=1),
        ])
        xml = score_to_musicxml(score)
        self.assertIn("<group-symbol>brace</group-symbol>", xml)
        self.assertIn('id="P2"', xml)
        self.assertIn("<sign>F</sign>", xml)
        restored = musicxml_to_score(xml)
        self.assertTrue(restored.piano)
        notes = [event for event in restored.events if event.midi is not None]
        self.assertEqual(sorted(event.staff for event in notes), [0, 1, 1, 1])

    def test_jianpu_two_staves_roundtrip(self):
        score = Score(piano=True, events=[
            NoteEvent(72, 0, 1, staff=0), NoteEvent(48, 0, 1, staff=1),
            NoteEvent(52, 0, 1, staff=1),
        ])
        text = score_to_jianpu(score)
        self.assertIn("R:", text)
        self.assertIn("L:", text)
        restored = jianpu_to_score(text)
        self.assertTrue(restored.piano)
        self.assertEqual([event.midi for event in restored.events if event.staff == 1], [48, 52])

    def test_musicxml_chord_start_beats(self):
        score = Score(piano=True, events=[
            NoteEvent(72, 0, 2), NoteEvent(60, 0, 2, staff=1),
            NoteEvent(64, 0, 2, staff=1),
        ])
        xml = score_to_musicxml(score)
        self.assertIn("<chord", xml)
        restored = musicxml_to_score(xml)
        left = [event for event in restored.events
                if event.staff == 1 and event.midi is not None]
        self.assertEqual([event.start_beat for event in left], [0.0, 0.0])

    def test_pdf_grand_staff(self):
        score = Score(piano=True, events=[NoteEvent(60, 0, 1), NoteEvent(48, 0, 1, staff=1)])
        pdf = score_to_pdf(score)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_pdf_jianpu_splits_hands(self):
        score = Score(piano=True, events=[
            NoteEvent(60, 0, 1, staff=0), NoteEvent(62, 1, 1, staff=0),
            NoteEvent(48, 0, 1, staff=1), NoteEvent(52, 1, 1, staff=1),
        ])
        drawn = []

        class Recorder:
            def setFont(self, *args):
                pass

            def drawString(self, x, y, text):
                drawn.append(text)

            def circle(self, *args, **kwargs):
                pass

            def setLineWidth(self, *args):
                pass

            def line(self, *args):
                pass

            def showPage(self):
                pass

        _draw_jianpu_section(Recorder(), score, _groups(score), 700,
                             "Helvetica", None, 842)
        self.assertIn("右手", drawn)
        self.assertIn("左手", drawn)
        self.assertEqual(drawn.count("||"), 2)

if __name__ == "__main__":
    unittest.main()
