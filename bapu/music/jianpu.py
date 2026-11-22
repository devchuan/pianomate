from __future__ import annotations

import re
from typing import List

from .layout import split_at_measures
from .model import NoteEvent, Score
from .theory import degree_to_midi, is_minor, midi_to_degree

_NOTE_RE = re.compile(r"^([b#]?)(['`,]*)([0-7])(['`,_.\-]*)$")
_TONIC_RE = re.compile(r"^1\s*=\s*([A-Ga-g][#b♯♭]?)(?:\s+(minor|major|m|小调|大调))?$", re.I)
_TEMPO_RE = re.compile(r"^(?:♩|bpm|=)\s*=?\s*(\d+(?:\.\d+)?)$", re.I)


def _parse_duration(marks: str) -> float:
    underlines = marks.count("_")
    dots = marks.count(".")
    duration = 1.0 / (2.0 ** underlines)
    if dots:
        duration *= 2.0 - 1.0 / (2.0 ** dots)
    return duration


def _parse_note_token(token: str, score: Score, events: List[NoteEvent],
                      staff: int = 0) -> bool:
    match = _NOTE_RE.match(token)
    if not match:
        return False
    accidental_text, leading, digit, marks = match.groups()
    accidental = 1 if accidental_text == "#" else -1 if accidental_text == "b" else 0
    octave = (leading.count("'") + marks.count("'")) - (leading.count(",") + marks.count(","))
    beats = _parse_duration(marks.replace("'", "").replace(",", "").replace("-", ""))
    beats += marks.count("-")
    digit = int(digit)
    if digit == 0:
        midi = None
        accidental = 0
        octave = 0
    else:
        midi = degree_to_midi(digit, score.tonic, score.mode, octave, accidental)
    cursor = sum(event.beats for event in events if event.staff == staff)
    events.append(NoteEvent(midi=midi, start_beat=cursor, beats=beats,
                            measure=int(cursor // score.beats_per_measure), staff=staff))
    return True


def parse_jianpu(text: str) -> Score:
    """解析简谱文本，例如：1=C 4/4 ♩=120\n1 2 3 4 | 5 - 6 - |"""
    score = Score()
    events: List[NoteEvent] = []
    current_staff = 0
    for raw_line in text.splitlines():
        for token in raw_line.replace("|", " ").split():
            if token.upper() in ("R:", "右手:"):
                current_staff = 0
                continue
            if token.upper() in ("L:", "左手:"):
                current_staff = 1
                continue
            tonic_match = _TONIC_RE.match(token)
            if tonic_match:
                tonic = tonic_match.group(1).replace("♯", "#").replace("♭", "b")
                score.tonic = tonic[0].upper() + tonic[1:]
                mode = (tonic_match.group(2) or "").lower()
                score.mode = "minor" if mode in ("m", "minor", "小调") else "major"
                continue
            if re.match(r"^\d+/\d+$", token):
                numerator, denominator = token.split("/")
                score.beats_per_measure = max(1, int(numerator))
                score.beat_unit = max(1, int(denominator))
                continue
            tempo_match = _TEMPO_RE.match(token)
            if tempo_match:
                score.tempo = max(20.0, min(300.0, float(tempo_match.group(1))))
                continue
            if token in ("-", "—"):
                if events:
                    last = events[-1]
                    events[-1] = NoteEvent(
                        midi=last.midi, start_beat=last.start_beat,
                        beats=last.beats + 1.0, measure=last.measure,
                        velocity=last.velocity, raw_onset=last.raw_onset,
                        raw_duration=last.raw_duration, tie=last.tie, staff=last.staff,
                    )
                else:
                    events.append(NoteEvent(midi=None, start_beat=0.0, beats=1.0,
                                            staff=current_staff))
                continue
            if _parse_note_token(token, score, events, current_staff):
                continue

    events = split_at_measures(events, score.beats_per_measure)
    score.events = events
    score.title = "简谱导入"
    score.piano = any(event.staff == 1 for event in events)
    score.source_duration = sum(event.beats for event in events) * 60.0 / score.tempo
    return score


def _duration_suffix(beats: float) -> str:
    eps = 1e-6
    if beats >= 1.0 - eps:
        whole = int(beats + eps)
        suffix = "-" * max(0, whole - 1)
        fraction = beats - whole
        if abs(fraction - 0.5) < eps:
            suffix += "."
        elif abs(fraction - 0.75) < eps:
            suffix += ".."
        return suffix
    underlines = 0
    base = 1.0
    while base > beats + eps and underlines < 4:
        base /= 2.0
        underlines += 1
    suffix = "_" * underlines
    remainder = beats - base
    if abs(remainder - base / 2.0) < eps:
        suffix += "."
    elif abs(remainder - base * 0.75) < eps:
        suffix += ".."
    return suffix


def score_to_jianpu(score: Score) -> str:
    header = f"1={score.tonic}"
    if is_minor(score.mode):
        header += " minor"
    header += f" {score.beats_per_measure}/{score.beat_unit} ♩={score.tempo:g}"
    lines = [header]
    events = sorted(score.events, key=lambda event: (event.measure, event.start_beat))
    max_measure = max((event.measure for event in events), default=0)
    staffs = (0, 1) if score.has_bass_staff else (0,)
    for measure in range(max_measure + 1):
        for staff in staffs:
            tokens: List[str] = []
            current = [e for e in events if e.measure == measure and (e.staff or 0) == staff]
            for event in current:
                if event.is_rest:
                    token = "0"
                else:
                    degree, accidental, octave = midi_to_degree(
                        int(event.midi), score.tonic, score.mode)
                    token = ("#" if accidental > 0 else "b" if accidental < 0 else "") + str(degree)
                    token += "'" * max(0, octave) + "," * max(0, -octave)
                token += _duration_suffix(event.beats)
                if event.tie in ("start", "continue"):
                    token += "~"
                tokens.append(token)
            prefix = ("R:" if staff == 0 else "L:") + " " if score.has_bass_staff else ""
            lines.append(prefix + " ".join(tokens)
                         + (" |" if measure < max_measure else " ||"))
    return "\n".join(lines)
