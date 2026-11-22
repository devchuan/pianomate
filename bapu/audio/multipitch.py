from __future__ import annotations

from dataclasses import dataclass
from typing import List

import librosa
import numpy as np

from ..music.theory import midi_to_freq

MIDI_MIN = 36
MIDI_MAX = 90


@dataclass
class ChordSegment:
    start: float
    end: float
    notes: List[tuple[int, int]]  # (midi, staff)
    velocity: float = 0.8


def _build_templates(sample_rate: int, n_fft: int,
                     midis: List[int]) -> np.ndarray:
    bin_hz = sample_rate / n_fft
    templates = np.zeros((len(midis), n_fft // 2 + 1), dtype=np.float64)
    for row, midi in enumerate(midis):
        fundamental = midi_to_freq(midi)
        for harmonic in range(1, 9):
            frequency = fundamental * harmonic
            if frequency >= sample_rate / 2:
                break
            position = frequency / bin_hz
            lower = int(np.floor(position))
            fraction = position - lower
            weight = 1.0 / harmonic
            if 0 <= lower < templates.shape[1]:
                templates[row, lower] += weight * (1.0 - fraction)
            if 0 <= lower + 1 < templates.shape[1]:
                templates[row, lower + 1] += weight * fraction
    return templates


def _frame_pitches(magnitude: np.ndarray, templates: np.ndarray,
                   midis: List[int], max_notes: int = 6,
                   threshold: float = 0.2) -> List[int]:
    """用贪婪匹配追踪从单帧频谱中分离同时发声的音高。"""
    residual = magnitude.copy()
    base = float(np.linalg.norm(magnitude)) + 1e-9
    norms = np.linalg.norm(templates, axis=1) + 1e-9
    chosen: List[int] = []
    selected: set[int] = set()
    for _ in range(max_notes):
        scores = (templates @ residual) / norms
        scores[list(selected)] = -np.inf
        index = int(np.argmax(scores))
        if scores[index] < threshold * base:
            break
        chosen.append(midis[index])
        selected.add(index)
        template = templates[index]
        coefficient = float(np.dot(residual, template) / np.dot(template, template))
        residual = np.maximum(0.0, residual - coefficient * template)
    return chosen


def estimate_chords(y: np.ndarray, sr: int, onsets: np.ndarray, duration: float,
                    *, hop: int = 256, n_fft: int = 4096,
                    split_midi: int = 60) -> List[ChordSegment]:
    """按 onset 分段估计每段内的同时发声音符，并按音高划分左右手。"""
    spectrum = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
    times = librosa.frames_to_time(np.arange(spectrum.shape[1]), sr=sr, hop_length=hop)
    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop)[0]
    peak = max(float(np.max(rms)), 1e-6)

    midis = list(range(MIDI_MIN, MIDI_MAX + 1))
    templates = _build_templates(sr, n_fft, midis)
    segments: List[ChordSegment] = []

    for index, start in enumerate(onsets):
        end = onsets[index + 1] if index + 1 < len(onsets) else duration
        if end - start < 0.05:
            continue
        frame_indices = np.flatnonzero((times >= start) & (times < end))
        if frame_indices.size == 0:
            continue
        counts: dict[int, int] = {}
        for frame in frame_indices:
            if rms[frame] < 0.08 * peak:
                continue
            for note in _frame_pitches(spectrum[:, frame], templates, midis):
                counts[note] = counts.get(note, 0) + 1
        if not counts:
            continue
        minimum_support = max(2, int(0.3 * frame_indices.size))
        chosen = sorted(note for note, count in counts.items() if count >= minimum_support)
        if not chosen:
            continue
        velocity = float(np.clip(np.mean(rms[frame_indices]) / peak, 0.15, 1.0))
        notes = [(note, 0 if note >= split_midi else 1) for note in chosen]
        segments.append(ChordSegment(float(start), float(end), notes, velocity))

    merged: List[ChordSegment] = []
    for segment in segments:
        if merged and abs(merged[-1].end - segment.start) < 0.16:
            previous = set(merged[-1].notes)
            current = set(segment.notes)
            similarity = len(previous & current) / max(1, len(previous | current))
            if similarity >= 0.6:
                merged[-1].end = segment.end
                continue
        merged.append(segment)
    return merged
