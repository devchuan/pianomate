from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]

MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09,
                          2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53,
                          2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

_MAJOR_FIFTHS = {
    "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
    "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6,
}


def is_minor(mode: str) -> bool:
    value = str(mode).strip().lower()
    return value in ("m", "min", "minor", "小调", "自然小调") or "minor" in value


def scale_steps(mode: str) -> List[int]:
    return MINOR_SCALE if is_minor(mode) else MAJOR_SCALE


def midi_to_freq(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def freq_to_midi(freq: float) -> float:
    if freq <= 0:
        return float("nan")
    return 69.0 + 12.0 * math.log2(freq / 440.0)


def midi_to_name(midi: int, tonic: str = "C") -> str:
    names = FLAT_NAMES if tonic in ("F", "Bb", "Eb", "Ab", "Db", "Gb") else NOTE_NAMES
    return f"{names[int(midi) % 12]}{int(midi) // 12 - 1}"


def name_to_pc(name: str) -> int:
    name = name.strip().replace("♯", "#").replace("♭", "b")
    base = name[0].upper()
    offset = 0
    if "#" in name or "♯" in name:
        offset = 1
    elif "b" in name or "♭" in name:
        offset = -1
    return (NOTE_NAMES.index(base) + offset) % 12 if base in NOTE_NAMES else 0


def degree_to_midi(degree: int, tonic: str, mode: str = "major",
                   octave_shift: int = 0, accidental: int = 0) -> int:
    root = name_to_pc(tonic)
    steps = scale_steps(mode)
    idx = (degree - 1) % 7
    octave = (degree - 1) // 7
    return 60 + root + steps[idx] + accidental + 12 * (octave + octave_shift)


def midi_to_degree(midi: int, tonic: str, mode: str = "major") -> Tuple[int, int, int]:
    """返回 (级数 1-7, 升降记号 -1/0/1, 相对中音区的八度偏移)。"""
    root = name_to_pc(tonic)
    steps = scale_steps(mode)
    interval = (int(midi) - root) % 12

    def signed_diff(semitone: int) -> int:
        d = (interval - semitone) % 12
        return d - 12 if d > 6 else d

    preferred_sign = -1 if interval in (3, 8, 10) else 1
    diffs = [signed_diff(step) for step in steps]

    def rank(index: int) -> tuple[int, int, int]:
        difference = diffs[index]
        matches_preference = 0 if difference == 0 or (difference > 0) == (preferred_sign > 0) else 1
        return (abs(difference), matches_preference, index)

    idx = min(range(7), key=rank)
    accidental = diffs[idx]
    reference = 60 + root + steps[idx] + accidental
    octave_shift = int(round((int(midi) - reference) / 12.0))
    return idx + 1, accidental, octave_shift


def detect_key(pitches: List[int]) -> Tuple[str, str]:
    """基于 Krumhansl-Schmuckler 音高分布估计调性与调式。"""
    if not pitches:
        return "C", "major"
    hist = np.zeros(12, dtype=float)
    for pitch in pitches:
        hist[int(pitch) % 12] += 1.0
    if hist.std() == 0:
        return "C", "major"

    best_score, best_tonic, best_mode = -2.0, "C", "major"
    for index, name in enumerate(NOTE_NAMES):
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            score = float(np.corrcoef(hist, np.roll(profile, index))[0, 1])
            if score > best_score:
                best_score, best_tonic, best_mode = score, name, mode
    return best_tonic, best_mode


def key_fifths(tonic: str, mode: str = "major") -> int:
    if not is_minor(mode):
        return _MAJOR_FIFTHS.get(tonic, 0)
    relative_major = NOTE_NAMES[(name_to_pc(tonic) + 3) % 12]
    return _MAJOR_FIFTHS.get(relative_major, 0)


def key_from_fifths(fifths: int, mode: str = "major") -> Tuple[str, str]:
    fifths = int(fifths)
    names = FLAT_NAMES if fifths < 0 else NOTE_NAMES
    tonic = names[(7 * fifths) % 12]
    if is_minor(mode):
        tonic = names[(7 * fifths + 9) % 12]
    return tonic, mode
