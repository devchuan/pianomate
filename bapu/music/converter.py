from __future__ import annotations

from .jianpu import parse_jianpu, score_to_jianpu
from .midi import parse_midi
from .model import Score
from .musicxml import generate_musicxml, parse_musicxml


def score_to_musicxml(score: Score) -> str:
    return generate_musicxml(score)


def musicxml_to_score(xml_text: str) -> Score:
    return parse_musicxml(xml_text)


def midi_to_score(data: bytes, title: str = "导入的 MIDI") -> Score:
    return parse_midi(data, title)


def jianpu_to_score(text: str) -> Score:
    return parse_jianpu(text)


def musicxml_to_jianpu(xml_text: str) -> str:
    return score_to_jianpu(parse_musicxml(xml_text))


def jianpu_to_musicxml(text: str) -> str:
    return generate_musicxml(parse_jianpu(text))
