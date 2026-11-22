from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .model import NoteEvent, PedalEvent, Score


@dataclass
class _NoteMsg:
    tick: int
    note: int
    channel: int
    velocity: int
    on: bool


def _read_vlq(data: bytes, pos: int) -> Tuple[int, int]:
    value = 0
    while True:
        if pos >= len(data):
            raise ValueError("MIDI 数据不完整（VLQ 越界）")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            return value, pos


def _parse_track(data: bytes, start: int, end: int) -> Tuple[List[_NoteMsg], List[Tuple[int, int]], List[Tuple[int, int, int]], str, List[Tuple[int, bool]], List[Tuple[int, bool]]]:
    """解析单条音轨，返回 (音符消息, tempo 事件, 拍号事件, 轨道名, 延音踏板事件, 软踏板事件)。"""
    notes: List[_NoteMsg] = []
    tempos: List[Tuple[int, int]] = []       # (tick, 每四分音符微秒数)
    timesigs: List[Tuple[int, int, int]] = []  # (tick, beats, beat_unit)
    cc64: List[Tuple[int, bool]] = []        # (tick, 是否踩下)
    cc67: List[Tuple[int, bool]] = []        # (tick, 是否踩下)
    name = ""
    tick = 0
    pos = start
    last_status = 0

    while pos < end:
        delta, pos = _read_vlq(data, pos)
        tick += delta

        status = data[pos]
        if status < 0x80:
            status = last_status
        else:
            pos += 1

        if status == 0xFF:  # meta event
            last_status = 0
            meta_type = data[pos]
            pos += 1
            length, pos = _read_vlq(data, pos)
            payload = data[pos:pos + length]
            pos += length
            if meta_type == 0x51 and length >= 3:  # set tempo
                us_per_quarter = (payload[0] << 16) | (payload[1] << 8) | payload[2]
                tempos.append((tick, us_per_quarter))
            elif meta_type == 0x58 and length >= 4:  # time signature
                beats = payload[0]
                unit = 1 << payload[1]
                timesigs.append((tick, beats, unit))
            elif meta_type == 0x03:
                try:
                    name = payload.decode("utf-8", errors="replace").strip("\x00").strip()
                except Exception:
                    name = ""
            continue

        if status in (0xF0, 0xF7):  # sysex
            last_status = 0
            length, pos = _read_vlq(data, pos)
            pos += length
            continue

        high = status & 0xF0
        if high == 0x80:  # note off
            last_status = status
            note = data[pos]
            vel = data[pos + 1]
            pos += 2
            notes.append(_NoteMsg(tick, note, status & 0x0F, vel, False))
        elif high == 0x90:  # note on
            last_status = status
            note = data[pos]
            vel = data[pos + 1]
            pos += 2
            notes.append(_NoteMsg(tick, note, status & 0x0F, vel, True))
        elif high == 0xB0:  # control change
            last_status = status
            controller = data[pos]
            value = data[pos + 1]
            pos += 2
            if controller == 64:  # 延音踏板（sustain pedal）
                cc64.append((tick, value >= 64))
            elif controller == 67:  # 软踏板（una corda）
                cc67.append((tick, value >= 64))
        elif high in (0xA0, 0xE0):  # 2 字节数据（aftertouch / pitch bend）
            last_status = status
            pos += 2
        elif high in (0xC0, 0xD0):  # 1 字节数据（program / channel pressure）
            last_status = status
            pos += 1
        else:
            # 未知状态字节：无法确定长度，直接中止当前轨道，避免死循环
            break

    return notes, tempos, timesigs, name, cc64, cc67


def _tick_to_seconds(tick: int, ppq: int, tempos: List[Tuple[int, int]]) -> float:
    """根据 tempo map 把 tick 换算成秒（累加方式，支持 tempo 变化）。"""
    seconds = 0.0
    prev_tick = 0
    spb = 0.5  # 默认 120 BPM
    for t, us_per_quarter in tempos:
        if t >= tick:
            break
        seconds += (t - prev_tick) / ppq * spb
        prev_tick = t
        spb = us_per_quarter / 1_000_000.0
    seconds += (tick - prev_tick) / ppq * spb
    return seconds


def parse_midi(data: bytes, title: str = "导入的 MIDI") -> Score:
    if len(data) < 14 or data[0:4] != b"MThd":
        raise ValueError("不是有效的标准 MIDI 文件")

    header_len = int.from_bytes(data[4:8], "big")
    fmt = int.from_bytes(data[8:10], "big")
    track_count = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big", signed=True)

    if division <= 0:
        # SMPTE 时基（罕见），退化为 480 PPQ
        ppq = 480
    else:
        ppq = division

    all_notes: List[_NoteMsg] = []
    all_tempos: List[Tuple[int, int]] = []
    timesigs: List[Tuple[int, int, int]] = []
    names: List[str] = []
    all_cc64: List[Tuple[int, bool]] = []
    all_cc67: List[Tuple[int, bool]] = []

    pos = 8 + header_len
    while pos < len(data) - 4:
        chunk_id = data[pos:pos + 4]
        chunk_len = int.from_bytes(data[pos + 4:pos + 8], "big")
        body_start = pos + 8
        body_end = body_start + chunk_len
        if chunk_id == b"MTrk":
            notes, tempos, sigs, name, cc64, cc67 = _parse_track(data, body_start, min(body_end, len(data)))
            all_notes.extend(notes)
            all_tempos.extend(tempos)
            timesigs.extend(sigs)
            all_cc64.extend(cc64)
            all_cc67.extend(cc67)
            if name:
                names.append(name)
        pos = body_end

    if not all_notes:
        raise ValueError("MIDI 文件中没有音符")

    all_tempos.sort()
    if not all_tempos:
        all_tempos = [(0, 500000)]  # 默认 120 BPM

    beats_per_measure, beat_unit = 4, 4
    if timesigs:
        beats_per_measure = max(1, timesigs[0][1])
        beat_unit = max(1, timesigs[0][2])

    # 初始速度：取首个 tempo
    first_tempo = 120.0
    if all_tempos:
        first_tempo = 60_000_000.0 / all_tempos[0][1]

    # 按 tick 排序后配对 note on/off；同一 tick 先 off 再 on（on=False 排在前面），
    # 避免同音连奏（前一音结束与后一音开始同 tick）被误配对
    all_notes.sort(key=lambda m: (m.tick, m.on))
    active: Dict[Tuple[int, int], Tuple[int, float, int, float]] = {}  # (ch,note) -> (tick, seconds, note, velocity)
    events: List[NoteEvent] = []

    for msg in all_notes:
        key = (msg.channel, msg.note)
        if msg.on and msg.velocity > 0:
            onset_seconds = _tick_to_seconds(msg.tick, ppq, all_tempos)
            active[key] = (msg.tick, onset_seconds, msg.note, msg.velocity / 127.0)
        else:
            onset = active.pop(key, None)
            if onset is None:
                continue
            onset_tick, onset_seconds, midi_note, velocity = onset
            end_seconds = _tick_to_seconds(msg.tick, ppq, all_tempos)
            duration_seconds = max(0.005, end_seconds - onset_seconds)
            beats = max(0.02, (msg.tick - onset_tick) / ppq)
            start_beat = onset_tick / ppq
            staff = 0 if midi_note >= 60 else 1
            measure = int(onset_tick // (ppq * beats_per_measure))
            events.append(NoteEvent(
                midi=midi_note,
                start_beat=start_beat,
                beats=beats,
                measure=measure,
                velocity=round(velocity, 4),
                raw_onset=round(onset_seconds, 4),
                raw_duration=round(duration_seconds, 4),
                staff=staff,
            ))

    # 尚未结束的音符（有 note on 无 note off），兜底生成一个短音
    for onset_tick, onset_seconds, midi_note, velocity in active.values():
        events.append(NoteEvent(
            midi=midi_note,
            start_beat=onset_tick / ppq,
            beats=0.5,
            measure=int(onset_tick // (ppq * beats_per_measure)),
            velocity=round(velocity, 4),
            raw_onset=round(onset_seconds, 4),
            raw_duration=0.5,
            staff=0 if midi_note >= 60 else 1,
        ))

    events.sort(key=lambda e: (e.start_beat, e.staff))
    source_duration = max((e.raw_onset + e.raw_duration for e in events), default=0.0)

    # 踏板事件：tick -> 秒，合并重复、按时间排序
    def _build_pedal(events_cc: List[Tuple[int, bool]]) -> List[PedalEvent]:
        result: List[PedalEvent] = []
        if events_cc:
            raw = sorted((round(_tick_to_seconds(t, ppq, all_tempos), 4), on) for t, on in events_cc)
            for time_s, on in raw:
                if result and abs(result[-1].time - time_s) < 0.001 and result[-1].on == on:
                    continue
                result.append(PedalEvent(time=time_s, on=on))
        return result

    pedal = _build_pedal(all_cc64)
    soft_pedal = _build_pedal(all_cc67)

    resolved_title = title
    if names:
        resolved_title = names[0] or title

    return Score(
        title=resolved_title,
        tempo=round(first_tempo, 2),
        beats_per_measure=beats_per_measure,
        beat_unit=beat_unit,
        tonic="C",
        mode="major",
        events=events,
        source_duration=round(source_duration, 4),
        piano=True,
        source="midi",
        pedal=pedal,
        soft_pedal=soft_pedal,
    )
