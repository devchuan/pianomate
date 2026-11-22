from __future__ import annotations

import io
from typing import Iterable

import numpy as np
import soundfile as sf

from ..music.model import Score
from ..music.theory import midi_to_freq

WAVEFORMS = ("sine", "triangle", "square", "sawtooth", "piano")


def _oscillator(t: np.ndarray, frequency: float, waveform: str) -> np.ndarray:
    angle = 2.0 * np.pi * frequency * t
    if waveform == "sine":
        return np.sin(angle)
    if waveform == "square":
        return np.sign(np.sin(angle))
    if waveform == "sawtooth":
        phase = angle / (2.0 * np.pi)
        return 2.0 * (phase - np.floor(phase + 0.5))
    if waveform == "piano":
        partials = sum(
            np.sin(harmonic * angle) / (harmonic ** 1.35)
            for harmonic in range(1, 6)
        )
        return partials / 1.9
    return (2.0 / np.pi) * np.arcsin(np.sin(angle))


def _envelope(length: int, sample_rate: int, seconds: float,
              waveform: str) -> np.ndarray:
    envelope = np.ones(length, dtype=float)
    attack = max(1, int(min(0.012, seconds * 0.2) * sample_rate))
    release = max(1, int(min(0.09, seconds * 0.35) * sample_rate))
    if attack + release > length:
        attack = max(1, length // 3)
        release = max(1, length - attack)
    envelope[:attack] = np.linspace(0.0, 1.0, attack)
    envelope[-release:] = envelope[-release:] * np.linspace(1.0, 0.0, release)
    if waveform == "piano":
        envelope *= np.exp(-np.arange(length) / sample_rate * 1.8)
    return envelope


def render_audio(score: Score, *, sample_rate: int = 22050,
                 waveform: str = "triangle", gain: float = 0.24) -> np.ndarray:
    """把乐谱合成为单声道浮点音频， MIDI 音高转换为对应频率。"""
    if waveform not in WAVEFORMS:
        raise ValueError(f"不支持的音色：{waveform}，可选 {', '.join(WAVEFORMS)}")
    beat_seconds = 60.0 / max(1.0, float(score.tempo))
    events: Iterable = score.events
    total_beats = max((event.start_beat + event.beats for event in events), default=0.0)
    length = max(int((total_beats * beat_seconds + 0.35) * sample_rate), int(0.2 * sample_rate))
    buffer = np.zeros(length, dtype=float)

    for event in events:
        if event.midi is None:
            continue
        start = int(max(0.0, event.start_beat * beat_seconds) * sample_rate)
        seconds = max(0.04, float(event.beats) * beat_seconds)
        note_length = min(int(seconds * sample_rate), length - start)
        if note_length <= 0:
            continue
        t = np.arange(note_length) / sample_rate
        tone = _oscillator(t, float(midi_to_freq(int(event.midi))), waveform)
        velocity = float(np.clip(event.velocity or 0.8, 0.15, 1.0))
        buffer[start:start + note_length] += (
            tone * _envelope(note_length, sample_rate, seconds, waveform) * velocity
        )

    peak = float(np.max(np.abs(buffer))) if buffer.size else 0.0
    if peak > 0:
        buffer = buffer / peak * min(1.0, max(0.05, gain))
    return buffer.astype(np.float32)


def score_to_wav(score: Score, *, sample_rate: int = 22050,
                 waveform: str = "triangle") -> bytes:
    """把乐谱合成为 16bit PCM WAV 字节，便于下载或播放。"""
    stream = io.BytesIO()
    sf.write(stream, render_audio(score, sample_rate=sample_rate, waveform=waveform),
             sample_rate, format="WAV", subtype="PCM_16")
    return stream.getvalue()
