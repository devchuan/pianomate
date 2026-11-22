from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List

from .layout import note_type_and_dots, split_at_measures
from .model import NoteEvent, Score
from .theory import FLAT_NAMES, NOTE_NAMES, key_fifths, key_from_fifths, name_to_pc, scale_steps

DIVISIONS = 480
# 谱面显示时值吸附网格：1/32 音符，避免 MIDI 真实时值产生大量 64 分碎音符
DISPLAY_GRID = 0.125


def _snap_grid(beats: float) -> float:
    return max(DISPLAY_GRID, round(beats / DISPLAY_GRID) * DISPLAY_GRID)


_TYPE_TO_BEATS = {
    "breve": 8.0, "whole": 4.0, "half": 2.0, "quarter": 1.0,
    "eighth": 0.5, "16th": 0.25, "32nd": 0.125, "64th": 0.0625,
}


def _pitch_parts(midi: int, tonic: str) -> tuple[str, int]:
    names = FLAT_NAMES if tonic in ("F", "Bb", "Eb", "Ab", "Db", "Gb") else NOTE_NAMES
    text = names[int(midi) % 12]
    alter = 1 if "#" in text else (-1 if "b" in text else 0)
    return text[0], alter


def _append_note(measure: ET.Element, event: NoteEvent,
                 score: Score, diatonic: set[int], is_chord: bool = False,
                 duration_beats: float | None = None, voice: int | None = None) -> None:
    note = ET.SubElement(measure, "note")
    if is_chord:
        ET.SubElement(note, "chord")
    if event.is_rest:
        ET.SubElement(note, "rest")
    else:
        step, alter = _pitch_parts(int(event.midi), score.tonic)
        pitch = ET.SubElement(note, "pitch")
        ET.SubElement(pitch, "step").text = step
        if alter:
            ET.SubElement(pitch, "alter").text = str(alter)
        ET.SubElement(pitch, "octave").text = str(int(event.midi) // 12 - 1)
        pc = int(event.midi) % 12
        if pc not in diatonic:
            ET.SubElement(note, "accidental").text = (
                "sharp" if alter > 0 else "flat" if alter < 0 else "natural")
    beats = event.beats if duration_beats is None else duration_beats
    # duration 用精确时值，保证 OSMD 光标时间轴与 start_beat 严格对齐（高亮同步）；
    # 记谱类型吸附到 1/32 网格，避免 MIDI 碎时值被画成 64 分音符小点
    ET.SubElement(note, "duration").text = str(max(1, int(round(beats * DIVISIONS))))
    if voice is not None:
        ET.SubElement(note, "voice").text = str(voice)
    note_type, dots = note_type_and_dots(_snap_grid(beats))
    ET.SubElement(note, "type").text = note_type
    for _ in range(dots):
        ET.SubElement(note, "dot")
    if event.tie in ("start", "continue"):
        ET.SubElement(note, "tie", type="start")
    if event.tie in ("stop", "continue"):
        ET.SubElement(note, "tie", type="stop")


def generate_musicxml(score: Score) -> str:
    root = ET.Element("score-partwise", version="4.0")
    work = ET.SubElement(root, "work")
    ET.SubElement(work, "work-title").text = score.title
    part_list = ET.SubElement(root, "part-list")

    grand_staff = score.has_bass_staff
    if grand_staff:
        group = ET.SubElement(part_list, "part-group", number="1", type="start")
        ET.SubElement(group, "group-symbol").text = "brace"
        ET.SubElement(group, "group-barline").text = "yes"

    layouts = [(0, "P1", "右手", "G", "2")]
    if grand_staff:
        layouts.append((1, "P2", "左手", "F", "4"))
    for staff, part_id, name, _, _ in layouts:
        score_part = ET.SubElement(part_list, "score-part", id=part_id)
        ET.SubElement(score_part, "part-name").text = name
    if grand_staff:
        ET.SubElement(part_list, "part-group", number="1", type="stop")

    events = split_at_measures(score.events, score.beats_per_measure)
    diatonic = {(name_to_pc(score.tonic) + step) % 12 for step in scale_steps(score.mode)}
    max_measure = max((event.measure for event in events), default=0)

    for staff, part_id, _, clef_sign, clef_line in layouts:
        part = ET.SubElement(root, "part", id=part_id)
        staff_events = [event for event in events if event.staff == staff]
        for number in range(1, max_measure + 2):
            measure = ET.SubElement(part, "measure", number=str(number))
            if number == 1:
                attrs = ET.SubElement(measure, "attributes")
                ET.SubElement(attrs, "divisions").text = str(DIVISIONS)
                key = ET.SubElement(attrs, "key")
                ET.SubElement(key, "fifths").text = str(key_fifths(score.tonic, score.mode))
                ET.SubElement(key, "mode").text = score.mode
                time = ET.SubElement(attrs, "time")
                ET.SubElement(time, "beats").text = str(score.beats_per_measure)
                ET.SubElement(time, "beat-type").text = str(score.beat_unit)
                clef = ET.SubElement(attrs, "clef")
                ET.SubElement(clef, "sign").text = clef_sign
                ET.SubElement(clef, "line").text = clef_line
                if staff == 0:
                    direction = ET.SubElement(measure, "direction", placement="above")
                    direction_type = ET.SubElement(direction, "direction-type")
                    metronome = ET.SubElement(direction_type, "metronome")
                    ET.SubElement(metronome, "beat-unit").text = "quarter"
                    ET.SubElement(metronome, "per-minute").text = f"{score.tempo:g}"

            measure_start = (number - 1) * score.beats_per_measure
            measure_end = measure_start + score.beats_per_measure
            current = sorted(
                (event for event in staff_events if event.measure == number - 1),
                key=lambda item: (item.start_beat, -(item.beats or 0)),
            )
            # 按 start_beat 归组：同一时刻的音符合为一个和弦，只推进一次时间轴
            groups: List[List[NoteEvent]] = []
            for event in current:
                if groups and abs(groups[-1][0].start_beat - event.start_beat) < 1e-4:
                    groups[-1].append(event)
                else:
                    groups.append([event])

            cursor = measure_start
            for group in groups:
                start = group[0].start_beat
                # 与上一个音之间的间隙用休止符填满，保证光标时间轴不漂移
                if start > cursor + 1e-4:
                    _append_note(measure, NoteEvent(midi=None, beats=start - cursor),
                                 score, diatonic, duration_beats=start - cursor)
                # 本和弦的显示时值：截断到下一个音的起点与小节末尾，避免重叠音累加。
                # duration 保持精确（光标时间轴才不会漂移），记谱类型的吸附在 _append_note 内完成。
                raw_end = max(event.start_beat + event.beats for event in group)
                next_start = measure_end
                for later in current:
                    if later.start_beat > start + 1e-4:
                        next_start = later.start_beat
                        break
                end = min(max(raw_end, start + 0.0625), next_start, measure_end)
                for index, event in enumerate(group):
                    ev_end = min(event.start_beat + event.beats, end)
                    ev_shown = max(0.0625, ev_end - start)
                    _append_note(measure, event, score, diatonic,
                                 is_chord=index > 0, duration_beats=ev_shown)
                cursor = max(cursor, end)
            remaining = measure_end - cursor
            if remaining > 1e-4:
                _append_note(measure, NoteEvent(midi=None, beats=remaining),
                             score, diatonic, duration_beats=remaining)

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
            '"http://www.musicxml.org/dtds/partwise.dtd">\n' + body + "\n")


def parse_musicxml(xml_text: str, title: str = "导入的五线谱") -> Score:
    root = ET.fromstring(xml_text)
    divisions = 1.0
    for node in root.iter("divisions"):
        try:
            divisions = float(node.text or "1")
        except ValueError:
            pass
        break
    if divisions <= 0:
        divisions = 1.0

    beats_per_measure, beat_unit = 4, 4
    for node in root.iter("time"):
        beats = node.findtext("beats")
        beat_type = node.findtext("beat-type")
        if beats:
            beats_per_measure = int(float(beats))
        if beat_type:
            beat_unit = int(float(beat_type))
        break

    fifths = 0
    mode = "major"
    for node in root.iter("key"):
        try:
            fifths = int(node.findtext("fifths") or "0")
        except ValueError:
            fifths = 0
        mode = node.findtext("mode") or mode
        break
    tonic, mode = key_from_fifths(fifths, mode)

    tempo = 120.0
    for node in root.iter("per-minute"):
        try:
            tempo = float(node.text or "120")
        except ValueError:
            pass
        break

    work_title = root.findtext("work/work-title")
    events: List[NoteEvent] = []
    parts = root.findall("part")
    piano = len(parts) > 1
    for staff_index, part in enumerate(parts):
        cursor = 0.0
        last_start = 0.0
        staff = 0 if staff_index == 0 else 1
        for measure in part.findall("measure"):
            try:
                measure_index = int(measure.get("number", "1")) - 1
            except ValueError:
                measure_index = 0
            for node in measure.findall("note"):
                is_chord = node.find("chord") is not None
                duration_text = node.findtext("duration") or "0"
                try:
                    beats = float(duration_text) / divisions
                except ValueError:
                    beats = 0.0
                if beats <= 0:
                    continue
                note_start = cursor if not is_chord else last_start
                rest_node = node.find("rest")
                midi = None
                if rest_node is None:
                    pitch = node.find("pitch")
                    if pitch is None:
                        continue
                    try:
                        step = pitch.findtext("step") or "C"
                        alter = float(pitch.findtext("alter") or 0)
                        octave = int(float(pitch.findtext("octave") or "4"))
                    except ValueError:
                        continue
                    midi = (octave + 1) * 12 + NOTE_NAMES.index(step) + int(alter)
                events.append(NoteEvent(midi=midi, start_beat=note_start, beats=beats,
                                        measure=measure_index, staff=staff))
                last_start = note_start
                if not is_chord:
                    cursor += beats

    return Score(title=work_title or title, tempo=tempo,
                 beats_per_measure=beats_per_measure, beat_unit=beat_unit,
                 tonic=tonic, mode=mode, events=events, piano=piano)
