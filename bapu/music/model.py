from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NoteEvent:
    """一个音符或休止符，start_beat/beats 以四分音符为单位。"""

    midi: Optional[int] = None  # None 表示休止符
    start_beat: float = 0.0
    beats: float = 1.0
    measure: int = 0
    velocity: float = 0.8
    raw_onset: float = 0.0
    raw_duration: float = 0.0
    tie: str = ""
    staff: int = 0  # 0=高音谱表/右手, 1=低音谱表/左手

    @property
    def is_rest(self) -> bool:
        return self.midi is None


@dataclass
class PedalEvent:
    """延音踏板事件，time 为相对曲首的秒。"""

    time: float = 0.0
    on: bool = False


@dataclass
class Score:
    title: str = "未命名"
    tempo: float = 120.0
    beats_per_measure: int = 4
    beat_unit: int = 4
    tonic: str = "C"
    mode: str = "major"
    events: List[NoteEvent] = field(default_factory=list)
    source_duration: float = 0.0
    piano: bool = False  # 是否使用钢琴大谱表
    source: str = ""  # "musicxml" / "midi" / "jianpu" 等，用于前端区分演奏模式
    pedal: List[PedalEvent] = field(default_factory=list)
    soft_pedal: List[PedalEvent] = field(default_factory=list)  # CC67 una corda 软踏板

    @property
    def has_bass_staff(self) -> bool:
        return self.piano or any(event.staff == 1 for event in self.events)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Score":
        events = [NoteEvent(**item) for item in data.get("events", [])]
        pedal = [PedalEvent(**item) for item in data.get("pedal", [])]
        soft_pedal = [PedalEvent(**item) for item in data.get("soft_pedal", [])]
        return cls(
            title=data.get("title", "未命名"),
            tempo=float(data.get("tempo", 120.0)),
            beats_per_measure=int(data.get("beats_per_measure", 4) or 4),
            beat_unit=int(data.get("beat_unit", 4) or 4),
            tonic=data.get("tonic", "C"),
            mode=data.get("mode", "major"),
            events=events,
            source_duration=float(data.get("source_duration", 0.0)),
            piano=bool(data.get("piano", False)),
            source=data.get("source", ""),
            pedal=pedal,
            soft_pedal=soft_pedal,
        )
