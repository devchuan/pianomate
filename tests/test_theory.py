import unittest

from bapu.music.theory import (
    degree_to_midi, detect_key, key_fifths, key_from_fifths,
    midi_to_degree, midi_to_name,
)


class TheoryTests(unittest.TestCase):
    def test_degree_roundtrip_major(self):
        for degree in range(1, 8):
            for octave in (-1, 0, 1):
                midi = degree_to_midi(degree, "C", "major", octave)
                parsed = midi_to_degree(midi, "C", "major")
                self.assertEqual(parsed, (degree, 0, octave))

    def test_degree_roundtrip_minor(self):
        for degree in range(1, 8):
            midi = degree_to_midi(degree, "A", "minor", 0)
            parsed = midi_to_degree(midi, "A", "minor")
            self.assertEqual(parsed[:2], (degree, 0))

    def test_accidental_roundtrip(self):
        midi = degree_to_midi(3, "C", "major", 0, accidental=-1)
        self.assertEqual(midi, 63)
        self.assertEqual(midi_to_degree(midi, "C", "major")[:2], (3, -1))

    def test_detect_major_key(self):
        tonic, mode = detect_key([60, 62, 64, 65, 67, 69, 71, 72, 71, 69, 67, 65])
        self.assertEqual((tonic, mode), ("C", "major"))

    def test_key_fifths_roundtrip(self):
        for tonic, mode in (("C", "major"), ("G", "major"), ("Bb", "major"),
                            ("A", "minor"), ("E", "minor"), ("D", "minor")):
            fifths = key_fifths(tonic, mode)
            restored, _ = key_from_fifths(fifths, mode)
            self.assertEqual(restored, tonic)

    def test_midi_name(self):
        self.assertEqual(midi_to_name(60), "C4")
        self.assertEqual(midi_to_name(61), "C#4")
        self.assertEqual(midi_to_name(61, "F"), "Db4")


if __name__ == "__main__":
    unittest.main()
