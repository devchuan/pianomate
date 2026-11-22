from __future__ import annotations

import math
from pathlib import Path
from typing import List, Tuple

import librosa
import numpy as np

from ..music.layout import split_at_measures
from ..music.model import NoteEvent, Score
from ..music.theory import detect_key
from .multipitch import ChordSegment, estimate_chords

SR = 22050
HOP = 256
N_FFT = 4096
DURATIONS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0)


def _estimate_tempo(onset_env: np.ndarray, sr: int, hop: int) -> float:
    try:
        candidates = librosa.feature.rhythm.tempo(
            onset_envelope=onset_env, sr=sr, hop_length=hop)
        tempo = float(np.asarray(candidates).flat[0])
    except Exception:
        tempo = float("nan")
    if not np.isfinite(tempo) or tempo < 40 or tempo > 240:
        tempo = 120.0
    return round(tempo, 1)


def _voiced_frames(y: np.ndarray, sr: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    fmin = float(librosa.note_to_hz("C2"))
    fmax = float(librosa.note_to_hz("C7"))
    f0 = librosa.yin(y, fmin=fmin, fmax=fmax, sr=sr,
                     frame_length=N_FFT, hop_length=HOP)
    midi = librosa.hz_to_midi(f0)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=HOP)[0]
    count = min(len(midi), len(rms))
    midi, rms = midi[:count], rms[:count]
    threshold = max(0.012, 0.12 * float(np.max(rms)) if rms.size else 0.0)
    valid = np.isfinite(midi) & (midi >= 34) & (midi <= 88) & (rms > threshold)
    return midi, rms, valid


def _segment_pitches(y: np.ndarray, sr: int, onsets: np.ndarray, duration: float,
                     polyphonic: bool) -> List[tuple[float, float, int, float] | None]:
    """返回每个时间片段的音符 (start, end, midi, velocity)，静音段为 None。"""
    midi, rms, valid = _voiced_frames(y, sr)
    frame_times = librosa.frames_to_time(np.arange(len(midi)), sr=sr, hop_length=HOP)
    peak = max(float(np.max(rms)), 1e-6) if rms.size else 1e-6

    magnitudes = None
    frequency_bins = None
    if polyphonic:
        spectrum = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
        frequency_bins, magnitudes = librosa.piptrack(
            S=spectrum, sr=sr, n_fft=N_FFT, hop_length=HOP,
            fmin=float(librosa.note_to_hz("C2")),
            fmax=float(librosa.note_to_hz("C7")), threshold=0.05)

    segments: List[tuple[float, float, int, float] | None] = []
    for index, start in enumerate(onsets):
        end = onsets[index + 1] if index + 1 < len(onsets) else duration
        if end - start < 0.05:
            continue
        selection = (frame_times >= start) & (frame_times < end)
        indices = np.flatnonzero(selection & valid)
        expected_frames = max(1, (end - start) * sr / HOP)
        ratio = len(indices) / expected_frames
        velocity = float(np.mean(rms[indices]) / peak) if len(indices) else 0.0
        velocity = float(np.clip(velocity, 0.15, 1.0))

        if not polyphonic:
            if len(indices) == 0 or ratio < 0.32:
                segments.append(None)
                continue
            pitch = int(round(float(np.median(midi[indices]))))
            segments.append((float(start), float(end), pitch, velocity))
            continue

        if magnitudes is None or len(indices) == 0:
            segments.append(None)
            continue
        pitch_counts: dict[int, int] = {}
        for frame_index in indices:
            bin_indices = np.flatnonzero(magnitudes[:, frame_index] > 0)
            for bin_index in bin_indices:
                freq = frequency_bins[bin_index, frame_index]
                if not np.isfinite(freq) or freq <= 0:
                    continue
                note = int(round(float(librosa.hz_to_midi(freq))))
                if 34 <= note <= 88:
                    pitch_counts[note] = pitch_counts.get(note, 0) + 1
        min_votes = max(2, int(0.25 * len(indices)))
        chosen = sorted((note for note, votes in pitch_counts.items() if votes >= min_votes),
                        key=lambda note: -pitch_counts[note])[:4]
        if not chosen:
            segments.append(None)
            continue
        for note in sorted(chosen):
            segments.append((float(start), float(end), note, velocity))

    return segments


def _quantize(segments: List[tuple[float, float, int, float] | None],
              tempo: float, beats_per_measure: int) -> List[NoteEvent]:
    beat_seconds = 60.0 / tempo
    grid = beat_seconds / 4.0

    def quantize_time(value: float) -> float:
        return round(value / grid) * grid

    raw: List[NoteEvent] = []
    for segment in segments:
        if segment is None:
            continue
        start, end, midi, velocity = segment
        q_start, q_end = quantize_time(start), quantize_time(end)
        if q_end - q_start < grid * 0.5:
            continue
        beats = round((q_end - q_start) / beat_seconds * 4) / 4
        nearest = min(DURATIONS, key=lambda candidate: abs(candidate - beats))
        if abs(nearest - beats) <= max(0.18, beats * 0.12):
            beats = nearest
        beats = float(np.clip(beats, 0.25, 16.0))
        raw.append(NoteEvent(midi=midi, start_beat=round(q_start / beat_seconds * 4) / 4,
                             beats=beats, velocity=velocity,
                             raw_onset=start, raw_duration=end - start))

    raw.sort(key=lambda event: (event.start_beat, -(event.midi or 0)))
    trimmed: List[NoteEvent] = []
    for event in raw:
        if trimmed and event.start_beat < trimmed[-1].start_beat + trimmed[-1].beats - 1e-6:
            overlap = trimmed[-1].start_beat + trimmed[-1].beats - event.start_beat
            trimmed[-1].beats = max(0.25, round((trimmed[-1].beats - overlap) * 4) / 4)
            if trimmed[-1].beats <= 0.25 and trimmed[-1].midi != event.midi:
                trimmed.pop()
        trimmed.append(event)

    events: List[NoteEvent] = []
    cursor = 0.0
    for event in trimmed:
        if event.start_beat - cursor >= 0.25:
            events.append(NoteEvent(midi=None, start_beat=cursor,
                                    beats=round((event.start_beat - cursor) * 4) / 4))
        events.append(event)
        cursor = event.start_beat + event.beats
    return events


def _quantize_chords(segments: List[ChordSegment], tempo: float,
                     beats_per_measure: int) -> List[NoteEvent]:
    beat_seconds = 60.0 / tempo
    grid = beat_seconds / 4.0
    grouped: dict[int, List[NoteEvent]] = {0: [], 1: []}

    for segment in segments:
        q_start = round(segment.start / grid) * grid
        q_end = round(segment.end / grid) * grid
        if q_end - q_start < grid * 0.5:
            continue
        beats = round((q_end - q_start) / beat_seconds * 4) / 4
        nearest = min(DURATIONS, key=lambda candidate: abs(candidate - beats))
        if abs(nearest - beats) <= max(0.18, beats * 0.12):
            beats = nearest
        beats = float(np.clip(beats, 0.25, 16.0))
        start_beat = round(q_start / beat_seconds * 4) / 4
        for midi, staff in segment.notes:
            grouped[staff].append(NoteEvent(
                midi=midi, start_beat=start_beat, beats=beats,
                velocity=segment.velocity, staff=staff,
                raw_onset=segment.start, raw_duration=segment.end - segment.start,
            ))

    events: List[NoteEvent] = []
    for staff in (0, 1):
        trimmed = sorted(grouped[staff], key=lambda event: event.start_beat)
        if not trimmed:
            continue
        cursor = 0.0
        for event in trimmed:
            if event.start_beat - cursor >= 0.25:
                events.append(NoteEvent(midi=None, start_beat=cursor,
                                        beats=round((event.start_beat - cursor) * 4) / 4,
                                        staff=staff))
            events.append(event)
            cursor = event.start_beat + event.beats
        last_beat = cursor
        measures = int(math.ceil(last_beat / beats_per_measure - 1e-6))
        missing = measures * beats_per_measure - last_beat
        if missing >= 0.25:
            events.append(NoteEvent(midi=None, start_beat=last_beat,
                                    beats=missing, staff=staff))
    return split_at_measures(events, beats_per_measure)


def _parse_key(key: str | None, detected: tuple[str, str]) -> tuple[str, str]:
    if not key:
        return detected
    text = key.strip()
    minor = text.lower().endswith("m") or "minor" in text.lower() or "小调" in text
    root = text[:-1] if text.lower().endswith("m") else text
    root = root.replace("minor", "").replace("小调", "").strip() or "C"
    return root[0].upper() + root[1:], "minor" if minor else "major"


def transcribe(source: str | Path, *, tempo: float | None = None,
               key: str | None = None, time_signature: tuple[int, int] = (4, 4),
               polyphonic: bool = False, title: str | None = None,
               piano: bool = False, split_midi: int = 60) -> Score:
    """识别单声道旋律（或简单和弦）的主音高、节奏、速度与调性。"""
    wav_path = Path(source)
    y, sr = librosa.load(str(wav_path), sr=SR, mono=True)
    if y.size == 0:
        raise ValueError("音频内容为空")
    y, _ = librosa.effects.trim(y, top_db=40)
    duration = float(len(y) / sr)
    if duration < 0.2:
        raise ValueError("音频太短，无法识别")

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=HOP, units="frames")
    onsets = librosa.frames_to_time(onset_frames, sr=sr, hop_length=HOP)
    onsets = np.concatenate(([0.0], np.asarray(onsets, dtype=float)))
    onsets = np.unique(np.clip(np.round(onsets, 4), 0.0, duration))
    keep = np.r_[True, np.diff(onsets) > (0.18 if piano else 0.07)]
    onsets = onsets[keep]

    bpm = float(tempo) if tempo else _estimate_tempo(onset_env, sr, HOP)
    bpm = float(np.clip(bpm, 40.0, 240.0))

    if piano:
        beat_seconds = 60.0 / bpm
        beat_onsets = np.arange(0.0, duration + 1e-6, beat_seconds)
        chords = estimate_chords(y, sr, beat_onsets, duration, split_midi=split_midi)
        events = _quantize_chords(chords, bpm, time_signature[0])
    else:
        segments = _segment_pitches(y, sr, onsets, duration, polyphonic)
        events = _quantize(segments, bpm, time_signature[0])
        if events:
            beats_per_measure = int(time_signature[0])
            last_beat = max(event.start_beat + event.beats for event in events)
            measures = int(math.ceil(last_beat / beats_per_measure - 1e-6))
            missing = measures * beats_per_measure - last_beat
            if missing >= 0.25:
                events.append(NoteEvent(midi=None, start_beat=last_beat, beats=missing))
        events = split_at_measures(events, time_signature[0])

    pitch_pool: List[int] = []
    for event in events:
        if event.midi is not None:
            pitch_pool.extend([int(event.midi)] * max(1, int(round(event.beats * 2))))
    detected_key = detect_key(pitch_pool)
    tonic, mode = _parse_key(key, detected_key)

    return Score(
        title=title or Path(source).stem,
        tempo=round(bpm, 1),
        beats_per_measure=int(time_signature[0]),
        beat_unit=int(time_signature[1]),
        tonic=tonic,
        mode=mode,
        events=events,
        source_duration=round(duration, 3),
        piano=piano,
    )
