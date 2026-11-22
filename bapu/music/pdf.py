from __future__ import annotations

import io
import math
from collections import defaultdict
from typing import Dict, List

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from .model import NoteEvent, Score
from .theory import FLAT_NAMES, NOTE_NAMES, midi_to_degree, scale_steps

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 42
MEASURE_WIDTH = 122
STAFF_SPACING = 8
ROW_HEIGHT = 86
LETTER_INDEX = {"C": 0, "D": 1, "E": 2, "F": 3, "G": 4, "A": 5, "B": 6}
FLAT_KEYS = ("F", "Bb", "Eb", "Ab", "Db", "Gb")

_font_name = None
_music_font = None


def _fonts() -> tuple[str, str | None]:
    global _font_name, _music_font
    if _font_name is None:
        try:
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            _font_name = "STSong-Light"
        except Exception:
            _font_name = "Helvetica"
        candidates = [
            r"C:\Windows\Fonts\seguisym.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Apple Symbols.ttf",
        ]
        for path in candidates:
            try:
                pdfmetrics.registerFont(TTFont("ScoreMusicFont", path))
                _music_font = "ScoreMusicFont"
                break
            except Exception:
                continue
    return _font_name, _music_font


def _diatonic(midi: int, tonic: str) -> tuple[str, int, int]:
    names = FLAT_NAMES if tonic in FLAT_KEYS else NOTE_NAMES
    text = names[int(midi) % 12]
    alter = 1 if "#" in text else -1 if "b" in text else 0
    octave = int(midi) // 12 - 1
    return text[0], alter, octave * 7 + LETTER_INDEX[text[0]]


def _draw_clef(c: canvas.Canvas, x: float, bottom: float,
               font: str | None, bass: bool = False) -> None:
    if font:
        c.setFont(font, 42)
        c.drawString(x, bottom - 36, "\U0001d122" if bass else "\U0001d11e")
        return
    spacing = STAFF_SPACING
    path = c.beginPath()
    path.moveTo(x + 0.3 * spacing, bottom - 0.5 * spacing)
    path.curveTo(x - 0.7 * spacing, bottom + 0.6 * spacing,
                 x - 0.5 * spacing, bottom + 2.4 * spacing,
                 x + 0.3 * spacing, bottom + 3.2 * spacing)
    path.curveTo(x + 1.2 * spacing, bottom + 4.3 * spacing,
                 x + 1.4 * spacing, bottom + 5.5 * spacing,
                 x + 0.6 * spacing, bottom + 6.3 * spacing)
    path.curveTo(x - 0.3 * spacing, bottom + 7.1 * spacing,
                 x - 1.2 * spacing, bottom + 6.1 * spacing,
                 x - 0.9 * spacing, bottom + 5.2 * spacing)
    path.curveTo(x - 0.6 * spacing, bottom + 4.2 * spacing,
                 x + 0.5 * spacing, bottom + 3.9 * spacing,
                 x + 1.2 * spacing, bottom + 4.5 * spacing)
    path.curveTo(x + 1.8 * spacing, bottom + 5.2 * spacing,
                 x + 1.4 * spacing, bottom + 6.4 * spacing,
                 x + 0.8 * spacing, bottom + 6.5 * spacing)
    c.setLineWidth(1.3)
    c.drawPath(path, stroke=1, fill=0)


def _draw_grand_brace(c: canvas.Canvas, x: float, top: float, bottom: float) -> None:
    """在谱表左侧绘制横跨两个谱表的大括号。"""
    middle = (top + bottom) / 2.0
    path = c.beginPath()
    path.moveTo(x + 14, top)
    path.curveTo(x - 6, top, x + 10, middle + 18, x, middle)
    path.curveTo(x + 10, middle - 18, x - 6, bottom, x + 14, bottom)
    path.curveTo(x + 2, bottom + 16, x + 12, middle - 12, x + 6, middle)
    path.curveTo(x + 12, middle + 12, x + 2, top - 16, x + 14, top)
    c.setFillColorRGB(0, 0, 0)
    c.drawPath(path, stroke=0, fill=1)


def _staff_note_y(staff_bottom: float, diatonic_index: int,
                  reference: int = 30) -> float:
    """reference 为最下方线的音级（高音谱表 E4=30，低音谱表 G2=18）。"""
    return staff_bottom + (diatonic_index - reference) * (STAFF_SPACING / 2)


def _draw_note(c: canvas.Canvas, x: float, y: float, beats: float,
               accidental: str) -> None:
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    if accidental:
        c.setFont("Helvetica-Bold", 13)
        c.drawString(x - 15, y - 4, accidental)
    whole = beats >= 4
    filled = beats < 2
    c.saveState()
    c.translate(x, y)
    c.rotate(-20)
    c.setLineWidth(1.4)
    c.ellipse(-7.2 if whole else -6, -4.8 if whole else -4,
              7.2 if whole else 6, 4.8 if whole else 4,
              stroke=0 if filled else 1, fill=1 if filled else 0)
    c.restoreState()
    c.setLineWidth(1.2)


def _draw_note_with_stem(c: canvas.Canvas, x: float, y: float, beats: float,
                         accidental: str, stem_up: bool) -> None:
    _draw_note(c, x, y, beats, accidental)
    if beats >= 4:
        return
    stem_x = x + 5.5 if stem_up else x - 5.5
    stem_end = y + 28 if stem_up else y - 28
    c.line(stem_x, y, stem_x, stem_end)
    if beats <= 0.5:
        flags = [(0, 0)] if beats <= 0.5 else []
        if beats <= 0.25:
            flags.append((9, 23))
        c.setLineWidth(1.8)
        for offset, tip in flags:
            path = c.beginPath()
            path.moveTo(stem_x, stem_end + (offset if stem_up else -offset))
            if stem_up:
                path.curveTo(stem_x + 10, stem_end + offset + 4,
                             stem_x + 10, stem_end + tip - 3,
                             stem_x + 8, stem_end + tip)
            else:
                path.curveTo(stem_x - 10, stem_end - offset - 4,
                             stem_x - 10, stem_end - tip + 3,
                             stem_x - 8, stem_end - tip)
            c.drawPath(path, stroke=1, fill=0)
    if beats in (0.75, 1.5, 3.0, 6.0):
        c.circle(x + 12, y, 1.6, stroke=0, fill=1)
    if beats in (1.75, 3.5, 7.0):
        c.circle(x + 12, y, 1.6, stroke=0, fill=1)
        c.circle(x + 18, y, 1.6, stroke=0, fill=1)


def _draw_rest(c: canvas.Canvas, x: float, staff_top: float, beats: float) -> None:
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    if beats >= 4:
        c.rect(x - 6, staff_top - 8, 12, 6, stroke=0, fill=1)
    elif beats >= 2:
        c.rect(x - 6, staff_top - 16, 12, 6, stroke=0, fill=1)
    else:
        path = c.beginPath()
        path.moveTo(x, staff_top - 8)
        path.lineTo(x + 6, staff_top - 14)
        path.lineTo(x, staff_top - 20)
        path.lineTo(x + 6, staff_top - 26)
        c.setLineWidth(1.8)
        c.drawPath(path, stroke=1, fill=0)


def _draw_header_meta(c: canvas.Canvas, x: float, y: float, score: Score,
                      font: str, music_font: str | None, size: float,
                      centered: bool = False) -> None:
    head = f"1={score.tonic}  {score.beats_per_measure}/{score.beat_unit}  "
    tail = f"={score.tempo:g}"
    if music_font:
        total = (pdfmetrics.stringWidth(head, font, size)
                 + pdfmetrics.stringWidth("♩", music_font, size * 1.15)
                 + pdfmetrics.stringWidth(tail, font, size))
    else:
        total = pdfmetrics.stringWidth(head + f"速度 {score.tempo:g}", font, size)
    x -= total / 2 if centered else 0
    c.setFont(font, size)
    c.drawString(x, y, head)
    x += pdfmetrics.stringWidth(head, font, size)
    if music_font:
        c.setFont(music_font, size * 1.15)
        c.drawString(x, y - 1, "♩")
        x += pdfmetrics.stringWidth("♩", music_font, size * 1.15)
        c.setFont(font, size)
        c.drawString(x, y, tail)
    else:
        c.drawString(x, y, f"速度 {score.tempo:g}")


def _groups(score: Score) -> Dict[int, List[NoteEvent]]:
    grouped: Dict[int, List[NoteEvent]] = defaultdict(list)
    for event in score.events:
        grouped[int(event.measure or 0)].append(event)
    for events in grouped.values():
        events.sort(key=lambda event: event.start_beat)
    return grouped


def _draw_staff_section(c: canvas.Canvas, score: Score, grouped: Dict[int, List[NoteEvent]],
                        y: float, font: str, music_font: str | None,
                        page_height: float) -> float:
    max_measure = max(grouped, default=0)
    beat_sec = 60.0 / max(1.0, score.tempo)
    steps = scale_steps(score.mode)
    root_pc = NOTE_NAMES.index(score.tonic) if score.tonic in NOTE_NAMES else 0
    diatonic_pcs = {(root_pc + step) % 12 for step in steps}

    c.setFont(font, 12)
    c.drawString(MARGIN, y, "五线谱")
    y -= 18

    has_bass = score.has_bass_staff
    staffs = (0, 1) if has_bass else (0,)
    row_height = 150 if has_bass else ROW_HEIGHT
    staff_gap = 62

    for row_start in range(0, max_measure + 1, 4):
        if y - row_height < MARGIN + 10:
            c.showPage()
            y = page_height - MARGIN
        row_top = y
        for col in range(4):
            measure = row_start + col
            if measure > max_measure:
                break
            x0 = MARGIN + col * MEASURE_WIDTH
            for staff in staffs:
                staff_top = row_top - staff * staff_gap
                staff_bottom = staff_top - 4 * STAFF_SPACING
                for line in range(5):
                    c.setLineWidth(0.7)
                    c.line(x0, staff_bottom + line * STAFF_SPACING,
                           x0 + MEASURE_WIDTH, staff_bottom + line * STAFF_SPACING)
                c.setLineWidth(1.2)
                if col == 0:
                    _draw_clef(c, x0 + 3, staff_top, music_font, bass=(staff == 1))
                    c.setFont("Helvetica-Bold", 13)
                    c.drawCentredString(x0 + 30, staff_top - 11, str(score.beats_per_measure))
                    c.drawCentredString(x0 + 30, staff_top - 26, str(score.beat_unit))
            lowest_bottom = row_top - (staffs[-1]) * staff_gap - 4 * STAFF_SPACING
            c.setLineWidth(1.2)
            c.line(x0, lowest_bottom, x0, row_top)
            c.line(x0 + MEASURE_WIDTH, lowest_bottom,
                   x0 + MEASURE_WIDTH, row_top)
            if has_bass and col == 0:
                _draw_grand_brace(c, x0 - 26, row_top + 4,
                                  row_top - staffs[-1] * staff_gap - 4 * STAFF_SPACING - 4)

            for staff in staffs:
                staff_top = row_top - staff * staff_gap
                staff_bottom = staff_top - 4 * STAFF_SPACING
                reference = 30 if staff == 0 else 18
                events = [event for event in grouped.get(measure, [])
                          if (event.staff or 0) == staff]
                padding = 42 if col == 0 else 12
                available = MEASURE_WIDTH - padding - 10
                for event in events:
                    beat_offset = max(0.0, event.start_beat - measure * score.beats_per_measure)
                    x = x0 + padding + 6 + (beat_offset / score.beats_per_measure) * (available - 12)
                    if event.midi is None:
                        _draw_rest(c, x, staff_top, event.beats)
                        continue
                    _, alter, dia = _diatonic(int(event.midi), score.tonic)
                    note_y = _staff_note_y(staff_bottom, dia, reference)
                    for ledger in range(reference + 10, dia + 1, 2):
                        ly = _staff_note_y(staff_bottom, ledger, reference)
                        c.setLineWidth(0.8)
                        c.line(x - 10, ly, x + 10, ly)
                    for ledger in range(reference - 2, dia - 1, -2):
                        ly = _staff_note_y(staff_bottom, ledger, reference)
                        c.setLineWidth(0.8)
                        c.line(x - 10, ly, x + 10, ly)
                    pc = int(event.midi) % 12
                    mark = ""
                    if pc not in diatonic_pcs:
                        mark = "#" if alter > 0 else "b" if alter < 0 else (
                            "b" if score.tonic in FLAT_KEYS else "#")
                    _draw_note_with_stem(c, x, note_y, event.beats, mark, dia <= reference + 4)
        y -= row_height
    return y - 6


def _jianpu_token(event: NoteEvent, score: Score) -> tuple[str, int, int]:
    if event.midi is None:
        return "0", 0, 0
    degree, accidental, octave = midi_to_degree(int(event.midi), score.tonic, score.mode)
    prefix = "#" if accidental > 0 else "b" if accidental < 0 else ""
    whole = int(event.beats)
    suffix = "-" * max(0, whole - 1)
    fraction = event.beats - whole
    if abs(fraction - 0.5) < 1e-6:
        suffix += "."
    elif abs(fraction - 0.75) < 1e-6:
        suffix += ".."
    if event.beats < 1:
        suffix = "_" * max(0, round(-math.log2(max(event.beats, 0.125))))
    return prefix + str(degree) + suffix, octave, 0


def _draw_jianpu_section(c: canvas.Canvas, score: Score, grouped: Dict[int, List[NoteEvent]],
                         y: float, font: str, music_font: str | None,
                         page_height: float) -> float:
    max_measure = max(grouped, default=0)
    c.setFont(font, 12)
    c.drawString(MARGIN, y, "简谱")
    y -= 18
    _draw_header_meta(c, MARGIN, y, score, font, music_font, 9)
    y -= 22

    has_bass = score.has_bass_staff
    staffs = (0, 1) if has_bass else (0,)
    measures_per_row = 1 if has_bass else 4
    label_width = 34 if has_bass else 0
    measure_width = (PAGE_WIDTH - 2 * MARGIN - label_width) / measures_per_row
    staff_row_gap = 26
    block_height = staff_row_gap * (len(staffs) - 1) + 30

    for row_start in range(0, max_measure + 1, measures_per_row):
        if y - block_height < MARGIN + 10:
            c.showPage()
            y = page_height - MARGIN
            c.setFont(font, 12)
            c.drawString(MARGIN, y, "简谱（续）")
            y -= 22
        for staff in staffs:
            row_y = y - staff * staff_row_gap
            if has_bass:
                c.setFont(font, 10)
                c.drawString(MARGIN, row_y + 2, "右手" if staff == 0 else "左手")
            for col in range(measures_per_row):
                measure = row_start + col
                if measure > max_measure:
                    break
                x = MARGIN + label_width + col * measure_width
                events = [event for event in grouped.get(measure, [])
                          if (event.staff or 0) == staff]
                for event in events:
                    text, octave, _ = _jianpu_token(event, score)
                    c.setFont(font, 14)
                    c.drawString(x, row_y, text)
                    width = max(16, pdfmetrics.stringWidth(text, font, 14) + 4)
                    if octave:
                        dot_y = row_y + 15 if octave > 0 else row_y - 6
                        for index in range(abs(octave)):
                            c.circle(x + 5 + index * 5, dot_y, 1.1, stroke=0, fill=1)
                    if event.beats < 1:
                        c.setLineWidth(1)
                        c.line(x, row_y - 2, x + min(width, 18), row_y - 2)
                        if event.beats <= 0.25:
                            c.line(x, row_y - 4, x + min(width, 18), row_y - 4)
                    x += width + 4
                c.setFont(font, 12)
                c.drawString(x + 3, row_y, "||" if measure == max_measure else "|")
        y -= block_height
    return y


def score_to_pdf(score: Score, *, title: str | None = None) -> bytes:
    """把乐谱排版为 A4 PDF，包含五线谱与简谱两栏。"""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    font, music_font = _fonts()
    c.setTitle(title or score.title or "乐谱")
    c.setAuthor("扒谱助手")

    y = PAGE_HEIGHT - MARGIN
    c.setFont(font, 17)
    c.drawCentredString(PAGE_WIDTH / 2, y, title or score.title or "乐谱")
    y -= 24
    _draw_header_meta(c, PAGE_WIDTH / 2, y, score, font, music_font, 10, centered=True)
    y -= 30

    grouped = _groups(score)
    y = _draw_staff_section(c, score, grouped, y, font, music_font, PAGE_HEIGHT)
    y -= 12
    _draw_jianpu_section(c, score, grouped, y, font, music_font, PAGE_HEIGHT)
    c.showPage()
    c.save()
    return buffer.getvalue()
