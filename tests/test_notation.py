import unittest

from bapu.music.converter import (
    jianpu_to_musicxml, jianpu_to_score, musicxml_to_jianpu,
    musicxml_to_score, score_to_jianpu, score_to_musicxml,
)
from bapu.music.layout import split_at_measures
from bapu.music.model import NoteEvent, Score


def sample_score():
    return Score(
        title="测试", tempo=108.0, beats_per_measure=4, beat_unit=4,
        tonic="C", mode="major",
        events=[
            NoteEvent(60, 0.0, 1.0), NoteEvent(64, 1.0, 0.5),
            NoteEvent(67, 1.5, 0.5), NoteEvent(72, 2.0, 2.0),
            NoteEvent(None, 4.0, 1.0), NoteEvent(65, 5.0, 3.0),
        ],
    )


class NotationTests(unittest.TestCase):
    def test_jianpu_roundtrip(self):
        score = sample_score()
        restored = jianpu_to_score(score_to_jianpu(score))
        original = [(e.midi, e.beats) for e in score.events]
        parsed = [(e.midi, e.beats) for e in restored.events]
        self.assertEqual(parsed, original)

    def test_musicxml_roundtrip(self):
        score = sample_score()
        restored = musicxml_to_score(score_to_musicxml(score))
        self.assertEqual([e.midi for e in restored.events], [e.midi for e in score.events])
        self.assertEqual([round(e.beats, 3) for e in restored.events],
                         [round(e.beats, 3) for e in score.events])
        self.assertEqual((restored.tonic, restored.tempo), ("C", 108.0))

    def test_conversion_between_formats(self):
        score = sample_score()
        text = musicxml_to_jianpu(score_to_musicxml(score))
        self.assertIn("1=C", text)
        self.assertIn("|", text)
        xml = jianpu_to_musicxml(score_to_jianpu(score))
        self.assertIn("<score-partwise", xml)
        self.assertIn("<pitch>", xml)

    def test_jianpu_octave_and_accidental(self):
        score = jianpu_to_score("1=C 4/4 ♩=100\n1' #1 b3 7, ||")
        self.assertEqual([e.midi for e in score.events], [72, 61, 63, 59])

    def test_split_at_measures_with_tie(self):
        events = [NoteEvent(60, 2.0, 4.0)]
        split = split_at_measures(events, 4)
        self.assertEqual([(e.beats, e.tie) for e in split], [(2.0, "start"), (2.0, "stop")])


if __name__ == "__main__":
    unittest.main()
