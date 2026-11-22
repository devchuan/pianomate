from __future__ import annotations

import unittest

from bapu.music.midi import parse_midi


def _vlq(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _track(*events: bytes) -> bytes:
    body = b"".join(events)
    return b"MTrk" + len(body).to_bytes(4, "big") + body


def _smf(tracks: list[bytes], division: int = 480) -> bytes:
    header = (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (1).to_bytes(2, "big")  # format 1
        + len(tracks).to_bytes(2, "big")
        + division.to_bytes(2, "big")
    )
    return header + b"".join(tracks)


def _meta(delta: int, meta_type: int, payload: bytes) -> bytes:
    return _vlq(delta) + bytes([0xFF, meta_type]) + _vlq(len(payload)) + payload


def _note_on(delta: int, note: int, velocity: int, channel: int = 0) -> bytes:
    return _vlq(delta) + bytes([0x90 | channel, note, velocity])


def _note_off(delta: int, note: int, channel: int = 0) -> bytes:
    return _vlq(delta) + bytes([0x80 | channel, note, 0])


def _cc(delta: int, controller: int, value: int, channel: int = 0) -> bytes:
    return _vlq(delta) + bytes([0xB0 | channel, controller, value])


class TestParseMidi(unittest.TestCase):
    def _basic(self) -> bytes:
        tempo_track = _track(
            _meta(0, 0x51, b"\x07\xa1\x20"),  # 120 BPM
            _meta(0, 0x58, bytes([4, 2, 24, 8])),  # 4/4
            _meta(0, 0x2F, b""),
        )
        note_track = _track(
            _note_on(0, 60, 100),
            _note_off(480, 60),
            _note_on(0, 64, 64),  # 同一 tick 上开始第二个音
            _note_off(480, 64),
            _meta(0, 0x2F, b""),
        )
        return _smf([tempo_track, note_track])

    def test_parse_basic(self) -> None:
        score = parse_midi(self._basic(), "test")
        self.assertEqual(score.source, "midi")
        self.assertEqual(score.tempo, 120.0)
        self.assertEqual(score.beats_per_measure, 4)
        self.assertEqual(score.beat_unit, 4)
        self.assertEqual(len(score.events), 2)
        first, second = score.events
        self.assertEqual(first.midi, 60)
        self.assertAlmostEqual(first.velocity, 100 / 127, places=3)
        self.assertAlmostEqual(first.start_beat, 0.0, places=4)
        self.assertAlmostEqual(first.beats, 1.0, places=4)
        self.assertAlmostEqual(first.raw_onset, 0.0, places=4)
        self.assertAlmostEqual(first.raw_duration, 0.5, places=4)
        self.assertEqual(first.staff, 0)
        self.assertAlmostEqual(second.start_beat, 1.0, places=4)
        self.assertEqual(second.midi, 64)
        self.assertEqual(second.staff, 0)

    def test_note_on_zero_velocity_ends_note(self) -> None:
        tempo_track = _track(_meta(0, 0x51, b"\x07\xa1\x20"), _meta(0, 0x2F, b""))
        note_track = _track(
            _note_on(0, 72, 90),
            _note_on(240, 72, 0),  # velocity 0 视为 note off
            _meta(0, 0x2F, b""),
        )
        score = parse_midi(_smf([tempo_track, note_track]), "t")
        self.assertEqual(len(score.events), 1)
        self.assertAlmostEqual(score.events[0].beats, 0.5, places=4)

    def test_invalid_data(self) -> None:
        with self.assertRaises(ValueError):
            parse_midi(b"not a midi file at all", "x")
        with self.assertRaises(ValueError):
            parse_midi(b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (0).to_bytes(2, "big") + (480).to_bytes(2, "big"), "empty")

    def test_repeated_same_note_legato(self) -> None:
        # 同音连奏：E4 在 tick 0 结束于 480，第二个 E4 于同一 tick 480 开始
        tempo_track = _track(_meta(0, 0x51, b"\x07\xa1\x20"), _meta(0, 0x2F, b""))
        note_track = _track(
            _note_on(0, 64, 80),
            _note_off(480, 64),
            _note_on(0, 64, 90),  # 同一 tick 480 开始
            _note_off(480, 64),
            _meta(0, 0x2F, b""),
        )
        score = parse_midi(_smf([tempo_track, note_track]), "t")
        self.assertEqual(len(score.events), 2)
        first, second = score.events
        self.assertAlmostEqual(first.start_beat, 0.0, places=4)
        self.assertAlmostEqual(first.beats, 1.0, places=4)
        self.assertAlmostEqual(first.velocity, 80 / 127, places=3)
        self.assertAlmostEqual(second.start_beat, 1.0, places=4)
        self.assertAlmostEqual(second.beats, 1.0, places=4)
        self.assertAlmostEqual(second.velocity, 90 / 127, places=3)

    def test_sustain_pedal(self) -> None:
        tempo_track = _track(_meta(0, 0x51, b"\x07\xa1\x20"), _meta(0, 0x2F, b""))
        note_track = _track(
            _note_on(0, 60, 80),
            _cc(0, 64, 127),      # 踩下踏板
            _note_off(480, 60),   # 琴键松开
            _cc(960, 64, 0),      # 松开踏板（tick 1440 = 1.5s @120bpm）
            _meta(0, 0x2F, b""),
        )
        score = parse_midi(_smf([tempo_track, note_track]), "t")
        self.assertEqual(len(score.pedal), 2)
        self.assertTrue(score.pedal[0].on)
        self.assertAlmostEqual(score.pedal[0].time, 0.0, places=3)
        self.assertFalse(score.pedal[1].on)
        self.assertAlmostEqual(score.pedal[1].time, 1.5, places=3)

    def test_left_right_split(self) -> None:
        tempo_track = _track(_meta(0, 0x51, b"\x07\xa1\x20"), _meta(0, 0x2F, b""))
        note_track = _track(
            _note_on(0, 48, 80),
            _note_off(480, 48),
            _note_on(0, 72, 80),
            _note_off(480, 72),
            _meta(0, 0x2F, b""),
        )
        score = parse_midi(_smf([tempo_track, note_track]), "t")
        low = next(e for e in score.events if e.midi == 48)
        high = next(e for e in score.events if e.midi == 72)
        self.assertEqual(low.staff, 1)
        self.assertEqual(high.staff, 0)


if __name__ == "__main__":
    unittest.main()
