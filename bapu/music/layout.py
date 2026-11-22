from __future__ import annotations

from dataclasses import replace
from typing import List

from .model import NoteEvent

EPS = 1e-6


def split_at_measures(events: List[NoteEvent], beats_per_measure: int) -> List[NoteEvent]:
    """把跨小节的音符切开并用 tie 连接，便于五线谱与简谱排版。"""
    result: List[NoteEvent] = []
    for event in events:
        start = max(0.0, float(event.start_beat))
        end = start + max(EPS, float(event.beats))
        while start < end - EPS:
            measure = int(start // beats_per_measure)
            boundary = (measure + 1) * beats_per_measure
            segment_end = min(end, boundary)
            duration = segment_end - start
            if duration <= EPS:
                break
            tie = ""
            if start > event.start_beat + EPS:
                tie = "continue" if segment_end < end - EPS else "stop"
            elif segment_end < end - EPS:
                tie = "start"
            result.append(replace(event, start_beat=start, beats=duration,
                                  measure=measure, tie=tie))
            start = segment_end
    return result


def note_type_and_dots(beats: float) -> tuple[str, int]:
    candidates = [
        (8.0, "breve"), (4.0, "whole"), (2.0, "half"), (1.0, "quarter"),
        (0.5, "eighth"), (0.25, "16th"), (0.125, "32nd"), (0.0625, "64th"),
    ]
    for value, name in candidates:
        if beats >= value - EPS:
            remaining = beats - value
            unit = value / 2.0
            dots = 0
            while remaining >= unit - EPS and dots < 3:
                remaining -= unit
                unit /= 2.0
                dots += 1
            if remaining < EPS:
                return name, dots
    return "quarter", 0
