const $ = (id) => document.getElementById(id);

const state = { score: null, id: null, key: null, results: [] };

const player = {
  context: null,
  master: null,
  bus: null,
  nodes: [],
  status: "stopped",
  sorted: [],
  next: 0,
  beat: 0,
  startTime: 0,
  lastTime: 0,
  timer: null,
  buffers: {},
  missing: new Set(),
  speed: 1,
  durationBeats: 0,
  keys: new Map(),
  noise: null,
  velocities: new Map(),
  chordCounts: new Map(),
  activeNotes: new Map(),
  groupWhen: new Map(),
  sourceShown: false,
  osmd: null,
  osmdCursor: null,
  osmdNoteNodes: [],
  osmdHighlights: [],
  osmdCursorMap: [],
  osmdStyles: new WeakMap(),
  osmdToken: 0,
  salamanderBases: {},
  pedalEvents: [],
  softPedalEvents: [],
  jpRenderers: [],
  JP_ACTIVE_KEY: Symbol("jpActiveKey"),
};

const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
const FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"];
const LETTER_INDEX = { C: 0, D: 1, E: 2, F: 3, G: 4, A: 5, B: 6 };
const FLAT_KEYS = ["F", "Bb", "Eb", "Ab", "Db", "Gb"];
const WHITE_PCS = [0, 2, 4, 5, 7, 9, 11];
const LOOKAHEAD = 0.18;
const SALAMANDER_LAYERS = ["v1", "v13"];
const SALAMANDER_NOTES = [
  { name: "A0", midi: 21 }, { name: "C1", midi: 24 }, { name: "D#1", midi: 27 },
  { name: "F#1", midi: 30 }, { name: "A1", midi: 33 }, { name: "C2", midi: 36 },
  { name: "D#2", midi: 39 }, { name: "F#2", midi: 42 }, { name: "A2", midi: 45 },
  { name: "C3", midi: 48 }, { name: "D#3", midi: 51 }, { name: "F#3", midi: 54 },
  { name: "A3", midi: 57 }, { name: "C4", midi: 60 }, { name: "D#4", midi: 63 },
  { name: "F#4", midi: 66 }, { name: "A4", midi: 69 }, { name: "C5", midi: 72 },
  { name: "D#5", midi: 75 }, { name: "F#5", midi: 78 }, { name: "A5", midi: 81 },
  { name: "C6", midi: 84 }, { name: "D#6", midi: 87 }, { name: "F#6", midi: 90 },
  { name: "A6", midi: 93 }, { name: "C7", midi: 96 }, { name: "D#7", midi: 99 },
  { name: "F#7", midi: 102 }, { name: "A7", midi: 105 }, { name: "C8", midi: 108 },
];

let toastTimer = null;
function toast(message, type = "") {
  const el = $("toast");
  el.textContent = message;
  el.dataset.type = type;
  el.hidden = false;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, type === "error" ? 5000 : 2800);
}

function hasMidi(event) {
  return Boolean(event) && event.midi !== null && event.midi !== undefined;
}

function isMidiScore() {
  return Boolean(state.score) && state.score.source === "midi";
}

function pedalDownAt(seconds) {
  // seconds 为相对曲首的秒；返回该时刻延音踏板是否踩下
  let down = false;
  for (const event of player.pedalEvents) {
    if (event.time > seconds) break;
    down = event.on;
  }
  return down;
}

function nextPedalRelease(seconds) {
  // 返回严格晚于 seconds 的第一次踏板松开时刻（相对曲首秒），无则返回 Infinity
  for (const event of player.pedalEvents) {
    if (!event.on && event.time > seconds) return event.time;
  }
  return Infinity;
}

function softPedalDownAt(seconds) {
  let down = false;
  for (const event of player.softPedalEvents) {
    if (event.time > seconds) break;
    down = event.on;
  }
  return down;
}

function nameToPc(name) {
  const clean = String(name || "C").replace("♯", "#").replace("♭", "b");
  const base = clean[0] ? clean[0].toUpperCase() : "C";
  let offset = 0;
  if (clean.includes("#")) offset = 1;
  if (clean.includes("b")) offset = -1;
  return ((LETTER_INDEX[base] ?? 0) + offset + 12) % 12;
}

function isMinor(mode) {
  const value = String(mode || "").toLowerCase();
  return value === "m" || value === "minor" || value.includes("小调");
}

function scaleSteps(mode) {
  return isMinor(mode) ? [0, 2, 3, 5, 7, 8, 10] : [0, 2, 4, 5, 7, 9, 11];
}

function spell(midi, tonic, mode) {
  const names = FLAT_KEYS.includes(tonic) ? FLAT_NAMES : SHARP_NAMES;
  const text = names[((midi % 12) + 12) % 12];
  const letter = text[0];
  const alter = text.includes("#") ? 1 : text.includes("b") ? -1 : 0;
  const octave = Math.floor(midi / 12) - 1;
  return { letter, alter, octave, dia: octave * 7 + LETTER_INDEX[letter] };
}

function midiToDegree(midi, tonic, mode) {
  const root = nameToPc(tonic);
  const steps = scaleSteps(mode);
  const interval = (((midi % 12) - root) % 12 + 12) % 12;
  let best = 0;
  let bestDiff = 99;
  for (let i = 0; i < 7; i++) {
    let diff = (interval - steps[i] + 12) % 12;
    if (diff > 6) diff -= 12;
    if (Math.abs(diff) < Math.abs(bestDiff)) {
      bestDiff = diff;
      best = i;
    }
  }
  const reference = 60 + root + steps[best] + bestDiff;
  return { degree: best + 1, acc: bestDiff, oct: Math.round((midi - reference) / 12) };
}

function durationSuffix(beats) {
  const eps = 1e-6;
  if (beats >= 1 - eps) {
    const whole = Math.floor(beats + eps);
    let suffix = "-".repeat(Math.max(0, whole - 1));
    const fraction = beats - whole;
    if (Math.abs(fraction - 0.5) < eps) suffix += ".";
    else if (Math.abs(fraction - 0.75) < eps) suffix += "..";
    return suffix;
  }
  let underlines = 0;
  let base = 1;
  while (base > beats + eps && underlines < 4) {
    base /= 2;
    underlines++;
  }
  let suffix = "_".repeat(underlines);
  const remainder = beats - base;
  if (Math.abs(remainder - base / 2) < eps) suffix += ".";
  else if (Math.abs(remainder - base * 0.75) < eps) suffix += "..";
  return suffix;
}

function svgNode(tag, attrs, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  if (text !== undefined) node.textContent = text;
  return node;
}

function sortedEvents(score) {
  return [...(score.events || [])].sort(
    (a, b) => a.measure - b.measure || a.start_beat - b.start_beat || (a.staff || 0) - (b.staff || 0));
}

async function renderStaff(score) {
  const host = $("staffView");
  host.innerHTML = "";
  host.classList.remove("osmd-mode");
  player.osmd = null;
  player.osmdCursor = null;
  player.osmdNoteNodes = [];
  player.osmdHighlights = [];
  const token = ++player.osmdToken;

  if (!window.opensheetmusicdisplay || !score.musicxml) {
    renderFallbackStaff(score);
    return;
  }

  try {
    const options = {
      autoResize: false,
      zoom: 0.9,
      drawTitle: false,
      drawComposer: false,
      drawPartNames: false,
      drawMeasureNumbers: false,
    };
    const osmd = new window.opensheetmusicdisplay.OpenSheetMusicDisplay(host, options);
    osmd.setOptions(options);
    await osmd.load(score.musicxml);
    if (token !== player.osmdToken) return;
    osmd.render();
    if (token !== player.osmdToken) return;

    host.classList.add("osmd-mode");
    player.osmd = osmd;
    player.osmdCursor = osmd.cursor || null;
    player.osmdNoteNodes = [];
    player.osmdHighlights = [];

    const noteNodes = Array.from(host.querySelectorAll(".vf-notehead, .notehead, [class*='notehead']"));
    const pitchedEvents = sortedEvents(score).filter(hasMidi);
    noteNodes.forEach((node, index) => {
      const event = pitchedEvents[index];
      if (!event) return;
      node.classList.add("osmd-note");
      player.osmdNoteNodes.push({ node, event });
    });
    if (player.osmdCursor) {
      buildOsmdCursorMap();
      player.osmdCursor.reset();
      player.osmdCursor.hide();
    }
  } catch (error) {
    console.warn("OSMD 渲染失败，回退内置 SVG", error);
    if (token === player.osmdToken) renderFallbackStaff(score);
  }
}

function renderFallbackStaff(score) {
  const host = $("staffView");
  host.innerHTML = "";
  const events = sortedEvents(score);
  const maxMeasure = events.reduce((max, event) => Math.max(max, event.measure || 0), 0);
  const hasBass = Boolean(score.piano) || events.some((event) => event.staff === 1);
  const staffs = hasBass ? [0, 1] : [0];
  const perRow = 4;
  const measureWidth = 220;
  const left = 86;
  const top = 34;
  const staffGap = 76;
  const rowHeight = hasBass ? 196 : 112;
  const rows = Math.floor(maxMeasure / perRow) + 1;
  const width = left + perRow * measureWidth + 30;
  const height = top + rows * rowHeight + 20;
  const svg = svgNode("svg", { width, height, viewBox: `0 0 ${width} ${height}` });
  const rootPc = nameToPc(score.tonic);
  const diatonic = new Set(scaleSteps(score.mode).map((step) => (rootPc + step) % 12));
  const musicFont = "Bravura, 'Noto Music', 'Segoe UI Symbol', serif";

  for (let measure = 0; measure <= maxMeasure; measure++) {
    const row = Math.floor(measure / perRow);
    const col = measure % perRow;
    const x0 = left + col * measureWidth;
    const rowTop = top + row * rowHeight;

    staffs.forEach((staff) => {
      const staffTop = rowTop + staff * staffGap;
      for (let line = 0; line < 5; line++) {
        svg.appendChild(svgNode("line", {
          x1: x0, y1: staffTop + line * 10,
          x2: x0 + measureWidth, y2: staffTop + line * 10,
          stroke: "#283044", "stroke-width": 1,
        }));
      }
      if (col === 0) {
        svg.appendChild(svgNode("text", {
          x: x0 + 4, y: staffTop + 48, "font-size": 54,
          "font-family": musicFont, fill: "#283044",
        }, staff === 0 ? "𝄞" : "𝄢"));
        svg.appendChild(svgNode("text", {
          x: x0 + 54, y: staffTop + 19, "font-size": 19,
          "font-weight": 700, "text-anchor": "middle", fill: "#283044",
        }, String(score.beats_per_measure)));
        svg.appendChild(svgNode("text", {
          x: x0 + 54, y: staffTop + 38, "font-size": 19,
          "font-weight": 700, "text-anchor": "middle", fill: "#283044",
        }, String(score.beat_unit)));
      }
    });

    const lowerBottom = rowTop + (staffs.length - 1) * staffGap + 40;
    svg.appendChild(svgNode("line", {
      x1: x0, y1: rowTop, x2: x0, y2: lowerBottom,
      stroke: "#283044", "stroke-width": 1.4,
    }));
    svg.appendChild(svgNode("line", {
      x1: x0 + measureWidth, y1: rowTop, x2: x0 + measureWidth, y2: lowerBottom,
      stroke: "#283044", "stroke-width": measure === maxMeasure ? 3 : 1.4,
    }));
    if (hasBass && col === 0) {
      const bx = x0 - 26;
      const braceTop = rowTop - 4;
      const braceBottom = rowTop + (staffs.length - 1) * staffGap + 44;
      const midY = (braceTop + braceBottom) / 2;
      const brace = `M ${bx + 14} ${braceTop} `
        + `C ${bx - 6} ${braceTop}, ${bx + 10} ${midY - 18}, ${bx} ${midY} `
        + `C ${bx + 10} ${midY + 18}, ${bx - 6} ${braceBottom}, ${bx + 14} ${braceBottom} `
        + `C ${bx + 2} ${braceBottom - 16}, ${bx + 12} ${midY + 12}, ${bx + 6} ${midY} `
        + `C ${bx + 12} ${midY - 12}, ${bx + 2} ${braceTop + 16}, ${bx + 14} ${braceTop} Z`;
      svg.appendChild(svgNode("path", { d: brace, fill: "#283044", stroke: "none" }));
    }

    staffs.forEach((staff) => {
      const staffTop = rowTop + staff * staffGap;
      const bottomY = staffTop + 40;
      const reference = staff === 0 ? 30 : 18;
      const noteEvents = events.filter((event) => (event.measure || 0) === measure
        && (event.staff || 0) === staff);
      const paddingLeft = col === 0 ? 76 : 16;
      const available = measureWidth - paddingLeft - 14;
      noteEvents.forEach((event) => {
        const beatOffset = Math.max(0, event.start_beat - measure * score.beats_per_measure);
        const x = x0 + paddingLeft + 8
          + (beatOffset / score.beats_per_measure) * (available - 16);
        const yForDia = (dia) => bottomY - (dia - reference) * 5;

        if (!hasMidi(event)) {
          if (event.beats >= 4) {
            svg.appendChild(svgNode("rect", {
              x: x - 6, y: staffTop + 8, width: 12, height: 6, fill: "#283044",
            }));
          } else if (event.beats >= 2) {
            svg.appendChild(svgNode("rect", {
              x: x - 6, y: staffTop + 18, width: 12, height: 6, fill: "#283044",
            }));
          } else {
            svg.appendChild(svgNode("path", {
              d: `M ${x} ${staffTop + 8} l 6 6 l -6 6 l 6 6`,
              fill: "none", stroke: "#283044", "stroke-width": 2,
            }));
          }
          return;
        }

        const sp = spell(event.midi, score.tonic, score.mode);
        const y = yForDia(sp.dia);
        const index = score.events.indexOf(event);
        for (let dia = reference + 10; dia <= sp.dia; dia += 2) {
          svg.appendChild(svgNode("line", {
            x1: x - 10, y1: yForDia(dia), x2: x + 10, y2: yForDia(dia),
            stroke: "#283044", "stroke-width": 1,
          }));
        }
        for (let dia = reference - 2; dia >= sp.dia; dia -= 2) {
          svg.appendChild(svgNode("line", {
            x1: x - 10, y1: yForDia(dia), x2: x + 10, y2: yForDia(dia),
            stroke: "#283044", "stroke-width": 1,
          }));
        }

        if (!diatonic.has(((event.midi % 12) + 12) % 12)) {
          const mark = sp.alter > 0 ? "#" : sp.alter < 0 ? "b" : FLAT_KEYS.includes(score.tonic) ? "b" : "#";
          svg.appendChild(svgNode("text", {
            x: x - 17, y: y + 5, "font-size": 18, "font-weight": 700, fill: "#283044",
          }, mark));
        }

        const whole = event.beats >= 4;
        const filled = event.beats < 2;
        svg.appendChild(svgNode("ellipse", {
          class: "staff-note",
          "data-note-idx": index,
          cx: x, cy: y, rx: whole ? 7.6 : 6.2, ry: whole ? 5 : 4.4,
          transform: `rotate(-20 ${x} ${y})`,
          fill: filled ? "#283044" : "none",
          stroke: "#283044", "stroke-width": filled ? 0 : 1.8,
        }));

        if (!whole) {
          const up = sp.dia <= reference + 4;
          const stemX = up ? x + 5.6 : x - 5.6;
          const stemEnd = up ? y - 30 : y + 30;
          svg.appendChild(svgNode("line", {
            x1: stemX, y1: up ? y - 1 : y + 1,
            x2: stemX, y2: stemEnd, stroke: "#283044", "stroke-width": 1.5,
          }));
          if (event.beats <= 0.5) {
            svg.appendChild(svgNode("path", {
              d: up ? `M ${stemX} ${stemEnd} q 9 6 7 16` : `M ${stemX} ${stemEnd} q -9 -6 -7 -16`,
              fill: "none", stroke: "#283044", "stroke-width": 2,
            }));
          }
          if (event.beats <= 0.25) {
            svg.appendChild(svgNode("path", {
              d: up ? `M ${stemX} ${stemEnd + 9} q 9 6 7 16` : `M ${stemX} ${stemEnd - 9} q -9 -6 -7 -16`,
              fill: "none", stroke: "#283044", "stroke-width": 2,
            }));
          }
        }

        const dotted = [0.75, 1.5, 3, 6].includes(event.beats);
        const doubleDotted = [0.875, 1.75, 3.5, 7].includes(event.beats);
        if (dotted || doubleDotted) {
          svg.appendChild(svgNode("circle", { cx: x + 13, cy: y - 2, r: 1.8, fill: "#283044" }));
          if (doubleDotted) svg.appendChild(svgNode("circle", { cx: x + 19, cy: y - 2, r: 1.8, fill: "#283044" }));
        }
        svg.appendChild(svgNode("circle", {
          class: "staff-highlight", cx: x, cy: y, r: 11,
          fill: "#1971c2", opacity: 0, "data-note-idx": index,
        }));
      });
    });
  }

  // 延音踏板记号：把踏板时间线（秒）映射到拍点，画在谱表下方
  const pedalEvents = (score.pedal || []).slice().sort((a, b) => a.time - b.time);
  if (pedalEvents.length) {
    const bpm = Math.max(1, score.beats_per_measure || 4);
    const tempo = Math.max(1, score.tempo || 120);
    const beatToX = (beat) => {
      const measure = Math.floor(beat / bpm);
      const row = Math.floor(measure / perRow);
      const col = measure % perRow;
      const x0 = left + col * measureWidth;
      const paddingLeft = col === 0 ? 76 : 16;
      const available = measureWidth - paddingLeft - 14;
      const beatOffset = Math.max(0, beat - measure * bpm);
      return x0 + paddingLeft + 8 + (beatOffset / bpm) * (available - 16);
    };
    const pedalY = (row) => top + row * rowHeight + (staffs.length - 1) * staffGap + 46;

    const spans = [];
    let openAt = null;
    for (const p of pedalEvents) {
      if (p.on && openAt === null) openAt = p.time;
      else if (!p.on && openAt !== null) { spans.push([openAt, p.time]); openAt = null; }
    }
    if (openAt !== null) {
      spans.push([openAt, Math.max(openAt + 0.5, score.source_duration || ((maxMeasure + 1) * bpm * 60 / tempo))]);
    }

    for (const [t0, t1] of spans) {
      const b0 = t0 * tempo / 60;
      const b1 = t1 * tempo / 60;
      const m0 = Math.floor(b0 / bpm);
      const m1 = Math.floor(b1 / bpm);
      for (let m = m0; m <= m1; m++) {
        const segStart = Math.max(b0, m * bpm);
        const segEnd = Math.min(b1, (m + 1) * bpm);
        if (segEnd <= segStart) continue;
        const row = Math.floor(m / perRow);
        const y = pedalY(row);
        const x1 = beatToX(segStart);
        const x2 = beatToX(segEnd);
        svg.appendChild(svgNode("line", { x1, y1: y, x2, y2: y, stroke: "#7c8698", "stroke-width": 3, "stroke-linecap": "round" }));
        svg.appendChild(svgNode("line", { x1, y1: y - 5, x2: x1, y2: y + 5, stroke: "#7c8698", "stroke-width": 2 }));
      }
      const endRow = Math.floor(Math.floor(b1 / bpm) / perRow);
      svg.appendChild(svgNode("text", {
        x: beatToX(b1) + 3, y: pedalY(endRow) - 5, "font-size": 15,
        "font-family": "Georgia, serif", fill: "#7c8698",
      }, "*"));
    }
  }

  host.appendChild(svg);
}

function renderJianpu(score) {
  const host = $("jianpuView");
  host.innerHTML = "";
  player.jpRenderers.forEach(({ inst }) => { try { inst && inst.destroy && inst.destroy(); } catch (_) {} });
  player.jpRenderers = [];
  const RC = window.jr && window.jr.JianpuSVGRender;
  const events = sortedEvents(score);
  const maxMeasure = events.reduce((max, event) => Math.max(max, event.measure || 0), 0);
  const key = (typeof nameToPc === "function") ? nameToPc(score.tonic) : 0;
  const beats = Math.max(1, score.beats_per_measure || 4);
  const beatUnit = Math.max(1, score.beat_unit || 4);
  const tempo = Math.max(1, score.tempo || 120);

  // MIDI 的真实 start_beat 含大量不规则间隙，直接喂给库会被补成满屏休止符。
  // 简谱靠"数字+时值线"表达节奏，这里把音符按序号连续铺排（start=前一个的结束），
  // 并截断超长音，得到干净的标准简谱；高亮仍用 _event 绑定真实音符。
  const makeNotes = (list) => {
    let cursor = 0;
    return list.filter(hasMidi).map((event) => {
      const length = clampNumber(Number(event.beats) || 0.125, 0.125, 4);
      const note = {
        start: cursor,
        length,
        pitch: Number(event.midi),
        intensity: clampNumber(Number(event.velocity) || 0.8, 0.2, 1),
        _event: event,
      };
      cursor += length;
      return note;
    });
  };

  // 每行按音符数自适应分行（库每个音符约 95px），使自然宽度贴合容器：不拉伸、不滚动、不重叠。
  const available = Math.max(400, (host.clientWidth || 1046) - 60);
  const maxNotesPerLine = Math.max(4, Math.floor(available / 95));
  const eventsByMeasure = new Map();
  events.forEach((event) => {
    const m = event.measure || 0;
    if (!eventsByMeasure.has(m)) eventsByMeasure.set(m, []);
    eventsByMeasure.get(m).push(event);
  });
  const lines = [];
  let current = [];
  let currentCount = 0;
  for (let m = 0; m <= maxMeasure; m += 1) {
    const measureEvents = eventsByMeasure.get(m) || [];
    const count = measureEvents.filter(hasMidi).length;
    if (current.length && currentCount + count > maxNotesPerLine) {
      lines.push(current);
      current = [];
      currentCount = 0;
    }
    current = current.concat(measureEvents);
    currentCount += count;
  }
  if (current.length) lines.push(current);

  lines.forEach((lineEvents) => {
    const row = document.createElement("div");
    row.className = "jp-line";
    const canvas = document.createElement("div");
    canvas.className = "jp-canvas";
    row.appendChild(canvas);
    host.appendChild(row);
    if (!RC || !lineEvents.length) return;
    try {
      const info = {
        notes: makeNotes(lineEvents),
        keySignatures: [{ start: 0, key }],
        timeSignatures: [{ start: 0, numerator: beats, denominator: beatUnit }],
        tempos: [{ start: 0, qpm: tempo }],
      };
      const inst = new RC(info, {
        noteHeight: 20,
        noteColor: "#1f2430",
        activeNoteColor: "#1971c2",
        showBarNumbers: false,
        showTempoMarking: false,
      }, canvas);
      player.jpRenderers.push({ inst, notes: info.notes });
    } catch (error) {
      console.warn("简谱渲染失败，跳过该行", error);
    }
  });
}

function highlightJianpu(beat) {
  const sounding = new Set();
  player.sorted.forEach((event) => { if (isSounding(event, beat)) sounding.add(event); });
  player.jpRenderers.forEach((entry) => {
    const note = entry.notes.find((item) => item._event && sounding.has(item._event));
    const key = note ? `${note.start}|${note.pitch}` : null;
    if (key === entry[player.JP_ACTIVE_KEY]) return;
    entry[player.JP_ACTIVE_KEY] = key;
    try { entry.inst.redraw(note || undefined, false); } catch (_) {}
  });
}

function clearJianpuHighlight() {
  player.jpRenderers.forEach((entry) => {
    if (entry[player.JP_ACTIVE_KEY] == null) return;
    entry[player.JP_ACTIVE_KEY] = null;
    try { entry.inst.redraw(undefined, false); } catch (_) {}
  });
}

function buildKeyboard(score) {
  const host = $("keyboard");
  host.innerHTML = "";
  player.keys.clear();
  const midis = (score.events || []).filter(hasMidi).map((event) => event.midi);
  const rawLow = midis.length ? Math.min(...midis) : 60;
  const rawHigh = midis.length ? Math.max(...midis) : 72;
  const low = Math.max(21, Math.min(rawLow, 96));
  const high = Math.max(low + 12, Math.min(rawHigh, 108));
  let startC = low - (((low % 12) + 12) % 12);
  let endB = high + (11 - (((high % 12) + 12) % 12));
  if (endB - startC < 23) {
    const center = Math.round((startC + endB) / 24) * 12;
    startC = Math.max(0, center - 12);
    endB = startC + 23;
  }
  endB = Math.min(endB, 119);
  const whites = [];
  const blacks = [];
  for (let midi = startC; midi <= endB; midi++) {
    const pc = ((midi % 12) + 12) % 12;
    if (WHITE_PCS.includes(pc)) whites.push(midi);
    else blacks.push({ midi, prev: midi - 1 });
  }
  const whitePct = 100 / whites.length;
  const blackPct = Math.min(4.6, whitePct * 0.62);
  whites.forEach((midi) => {
    const key = document.createElement("div");
    key.className = "key white";
    key.dataset.midi = midi;
    if (((midi % 12) + 12) % 12 === 0) {
      const label = document.createElement("span");
      label.className = "key-label";
      label.textContent = "C" + (Math.floor(midi / 12) - 1);
      key.appendChild(label);
    }
    host.appendChild(key);
    player.keys.set(midi, key);
  });
  blacks.forEach(({ midi, prev }) => {
    const index = whites.indexOf(prev);
    if (index < 0) return;
    const key = document.createElement("div");
    key.className = "key black";
    key.dataset.midi = midi;
    key.style.width = blackPct + "%";
    key.style.left = `calc(${(index + 1) * whitePct}% - ${blackPct / 2}%)`;
    host.appendChild(key);
    player.keys.set(midi, key);
  });
}

function playableEvents() {
  let events = (state.score && state.score.events || []).filter(hasMidi);
  const part = $("partSelect").value;
  if (part !== "all") events = events.filter((event) => (event.staff || 0) === Number(part));
  return events;
}

function midiToFrequency(midi) {
  return 440 * Math.pow(2, (midi - 69) / 12);
}

function clampNumber(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function createReverbImpulse(ctx) {
  const seconds = 1.5;
  const length = Math.floor(ctx.sampleRate * seconds);
  const impulse = ctx.createBuffer(2, length, ctx.sampleRate);
  for (let channel = 0; channel < 2; channel += 1) {
    const data = impulse.getChannelData(channel);
    let smooth = 0;
    for (let i = 0; i < length; i += 1) {
      const white = Math.random() * 2 - 1;
      smooth = smooth * 0.72 + white * 0.28;
      data[i] = smooth * Math.pow(1 - i / length, 2.8);
    }
  }
  return impulse;
}

function ensureAudio() {
  if (!player.context) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error("当前浏览器不支持 Web Audio");
    const ctx = new AudioContextClass();
    player.context = ctx;

    player.bus = ctx.createGain();

    const lowShelf = ctx.createBiquadFilter();
    lowShelf.type = "lowshelf";
    lowShelf.frequency.value = 140;
    lowShelf.gain.value = -2;

    const presence = ctx.createBiquadFilter();
    presence.type = "peaking";
    presence.frequency.value = 2600;
    presence.Q.value = 0.9;
    presence.gain.value = 1.2;

    const highShelf = ctx.createBiquadFilter();
    highShelf.type = "highshelf";
    highShelf.frequency.value = 9000;
    highShelf.gain.value = -1.5;

    const compressor = ctx.createDynamicsCompressor();
    compressor.threshold.value = -12;
    compressor.knee.value = 14;
    compressor.ratio.value = 2;
    compressor.attack.value = 0.012;
    compressor.release.value = 0.32;

    player.master = ctx.createGain();
    player.master.gain.value = Number($("volumeInput").value || 80) / 17;

    const dry = ctx.createGain();
    dry.gain.value = 0.9;
    const send = ctx.createGain();
    send.gain.value = 0.26;
    const convolver = ctx.createConvolver();
    convolver.buffer = createReverbImpulse(ctx);
    const wet = ctx.createGain();
    wet.gain.value = 0.5;

    player.bus.connect(lowShelf);
    lowShelf.connect(presence);
    presence.connect(highShelf);
    highShelf.connect(compressor);
    compressor.connect(dry).connect(player.master);
    compressor.connect(send).connect(convolver).connect(wet).connect(player.master);
    player.master.connect(ctx.destination);
  }
  if (player.context.state === "suspended" && player.status !== "paused") {
    player.context.resume().catch(() => {});
  }
  return player.context;
}

function outputFor(ctx, panValue) {
  if (ctx.createStereoPanner) {
    const panner = ctx.createStereoPanner();
    panner.pan.value = clampNumber(panValue || 0, -0.35, 0.35);
    panner.connect(player.bus);
    return panner;
  }
  return player.bus;
}

function panForEvent(staff) {
  if (Number(staff) === 0) return 0.07;
  if (Number(staff) === 1) return -0.07;
  return 0;
}

function noteGainPeak(midi, velocity, staff, startBeat) {
  const count = player.chordCounts.get(startBeat) || 1;
  let level = 0.5 * (0.55 + 0.55 * clampNumber(velocity || 0.8, 0.2, 1));
  if (Number(staff) === 1) level *= 0.95;
  if (midi < 50) level *= 0.92;
  if (midi > 84) level *= 0.9;
  level /= Math.pow(count, 0.28);
  return clampNumber(level, 0.03, 0.75);
}

function buildExpressiveVelocities(events) {
  const beats = Math.max(1, (state.score && state.score.beats_per_measure) || 4);
  const counts = new Map();
  events.forEach((event) => {
    const beat = event.start_beat || 0;
    counts.set(beat, (counts.get(beat) || 0) + 1);
  });
  player.chordCounts = counts;

  const result = new Map();
  events.forEach((event, index) => {
    const beat = event.start_beat || 0;
    const measure = Math.floor(beat / beats);
    const beatInMeasure = beat % beats;
    const beatFraction = beatInMeasure - Math.floor(beatInMeasure);
    const phraseShape = Math.sin(Math.PI * ((measure % 4) / 3));
    let value = Number(event.velocity) || 0.78;

    if (beatInMeasure < 0.02) value += 0.08;
    else if (Math.abs(beatInMeasure - beats / 2) < 0.02) value += 0.03;
    if (beatFraction > 0.35 && beatFraction < 0.65) value -= 0.05;
    if (beatInMeasure >= beats - 0.5 && (Number(event.beats) || 0) <= 0.5) value -= 0.07;
    if (Number(event.staff) === 0 && (Number(event.beats) || 0) >= 0.5) value += 0.04;
    value += phraseShape * 0.05;
    if (Number(event.staff) === 1) value -= 0.04;
    if (event.midi < 52) value -= 0.04;
    if (event.midi > 84) value -= 0.03;
    value /= Math.pow(counts.get(beat) || 1, 0.3);
    if (index === 0) value += 0.03;

    result.set(event, clampNumber(value, 0.35, 0.98));
  });
  return result;
}

function humanizeOffset(index, event) {
  if (isMidiScore()) return 0;  // MIDI 已含真人时值，不再叠加人工抖动
  const beats = Math.max(1, (state.score && state.score.beats_per_measure) || 4);
  const beat = Number(event.start_beat) || 0;
  const position = beat % beats;
  const fraction = position - Math.floor(position);
  const length = Number(event.beats) || 0.5;
  let offset = 0;
  if (fraction > 0.3 && fraction < 0.7) offset += 0.012;
  if (position < 0.02) offset -= 0.004;
  if (position > beats - 0.55) offset += 0.006;
  if (length <= 0.5) offset += 0.004;
  if (length >= 2) offset -= 0.003;
  offset += (((index * 13) % 7) - 3) * 0.0015;
  return clampNumber(offset, -0.014, 0.018);
}

function articulatedSeconds(event, rate) {
  const length = Number(event.beats) || 0.5;
  if (isMidiScore()) return Math.max(0.04, length / rate);  // 忠实回放 MIDI 原始时长
  const beats = Math.max(1, (state.score && state.score.beats_per_measure) || 4);
  const position = (Number(event.start_beat) || 0) % beats;
  let factor = 1;
  if (length <= 0.25) factor = 0.86;
  else if (position > beats - 0.55 && length <= 0.5) factor = 0.78;
  else if (length <= 0.5) factor = 0.96;
  else if (length >= 2) factor = 1.06;
  return Math.max(0.08, length / rate * factor);
}

function salamanderBaseFor(midi) {
  let best = SALAMANDER_NOTES[0];
  let bestDistance = Infinity;
  SALAMANDER_NOTES.forEach((entry) => {
    const distance = Math.abs(entry.midi - midi);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = entry;
    }
  });
  return best;
}

function hasSalamanderSamples() {
  return Object.keys(player.buffers).some((key) => key.indexOf("sala:") === 0);
}

function layerForVelocity(velocity) {
  return clampNumber(velocity || 0.8, 0.2, 1) >= 0.72 ? "v13" : "v1";
}

function salamanderCdnUrl(name, suffix) {
  const layer = suffix === "v13" ? "13" : "1";
  return `https://unpkg.com/@audio-samples/piano-mp3-velocity${layer}@1.0.5/audio/${encodeURIComponent(name)}${suffix}.mp3`;
}

async function loadSalamanderSamples(ctx, midis) {
  const bases = [...new Set(midis.map((midi) => salamanderBaseFor(midi).name))];
  const jobs = [];
  bases.forEach((name) => {
    SALAMANDER_LAYERS.forEach((suffix) => {
      jobs.push((async () => {
        const key = `sala:${name}:${suffix}`;
        if (player.buffers[key] || player.missing.has(key)) return;
        const file = `${encodeURIComponent(name)}${suffix}.mp3`;
        const urls = [`/static/samples/salamander/${file}`, salamanderCdnUrl(name, suffix)];
        for (const url of urls) {
          try {
            const response = await fetch(url);
            if (!response.ok) throw new Error("missing");
            player.buffers[key] = await ctx.decodeAudioData(await response.arrayBuffer());
            return;
          } catch (_) {
            player.missing.add(key);
          }
        }
      })());
    });
  });
  await Promise.all(jobs);
}

function findSalamanderSample(midi, suffix) {
  let best = null;
  let bestDistance = Infinity;
  SALAMANDER_NOTES.forEach((entry) => {
    const key = `sala:${entry.name}:${suffix}`;
    if (!player.buffers[key]) return;
    const distance = Math.abs(entry.midi - midi);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = { entry, key };
    }
  });
  if (!best || bestDistance > 12) return null;
  return {
    buffer: player.buffers[best.key],
    rate: Math.pow(2, (midi - best.entry.midi) / 12),
    label: "salamander",
    layer: suffix,
  };
}

function nearestSample(midi, velocity) {
  const preferred = layerForVelocity(velocity);
  const order = preferred === "v13" ? ["v13", "v1"] : ["v1", "v13"];
  for (const suffix of order) {
    const hit = findSalamanderSample(midi, suffix);
    if (hit) return hit;
  }
  if (player.buffers[midi]) return { buffer: player.buffers[midi], rate: 1, label: "local" };
  let bestMidi = null;
  let bestDistance = Infinity;
  Object.keys(player.buffers).forEach((key) => {
    const candidate = Number(key);
    if (!Number.isFinite(candidate)) return;
    const distance = Math.abs(candidate - midi);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestMidi = candidate;
    }
  });
  if (bestMidi === null || bestDistance > 12) return null;
  return { buffer: player.buffers[bestMidi], rate: Math.pow(2, (midi - bestMidi) / 12), label: "local" };
}

async function loadLocalSamples(ctx, midis) {
  const unique = [...new Set(midis.map((midi) => clampNumber(midi, 36, 96)))];
  await Promise.all(unique.map(async (midi) => {
    if (player.buffers[midi] || player.missing.has(midi)) return;
    try {
      const response = await fetch(`/static/samples/piano/${midi}.wav`);
      if (!response.ok) throw new Error("missing");
      player.buffers[midi] = await ctx.decodeAudioData(await response.arrayBuffer());
    } catch (_) {
      player.missing.add(midi);
    }
  }));
}

function noiseBuffer(ctx) {
  if (!player.noise) {
    const length = Math.floor(ctx.sampleRate * 0.16);
    const buffer = ctx.createBuffer(1, length, ctx.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < length; i++) data[i] = Math.random() * 2 - 1;
    player.noise = buffer;
  }
  return player.noise;
}

function releaseActiveNote(key, when) {
  const active = player.activeNotes.get(key);
  if (!active) return;
  try {
    active.gain.gain.cancelScheduledValues(when);
    active.gain.gain.setTargetAtTime(0.0001, when, 0.035);
    active.source.stop(when + 0.18);
  } catch (_) {}
  player.activeNotes.delete(key);
}

function scheduleSample(ctx, start, duration, midi, velocity, staff, startBeat) {
  const sample = nearestSample(midi, velocity);
  if (!sample) {
    scheduleTone(ctx, start, duration, midi, "piano", velocity, staff, startBeat);
    return;
  }
  const key = `${staff}:${midi}`;
  releaseActiveNote(key, start);

  const source = ctx.createBufferSource();
  source.buffer = sample.buffer;
  source.playbackRate.value = sample.rate;
  const gain = ctx.createGain();
  const natural = sample.buffer.duration / sample.rate;
  const professional = sample.label === "salamander";
  const hold = Math.max(0.045, Math.min(duration, natural));
  const release = professional
    ? clampNumber(hold * 1.45, 0.55, 1.9)
    : clampNumber(hold * 0.8, 0.25, 1.0);
  const layerGain = sample.layer === "v13" ? 0.6 : 0.78;
  let peak = noteGainPeak(midi, velocity, staff, startBeat) * (professional ? layerGain : 0.9);
  const attack = professional ? Math.min(0.008, hold * 0.2) : Math.min(0.012, hold * 0.25);
  const onsetSongSec = (startBeat || 0) / beatsPerSecond();

  // 软踏板（una corda）：踩下时音量略降、加低通柔化
  const soft = isMidiScore() && player.softPedalEvents.length && softPedalDownAt(onsetSongSec);
  if (soft) peak *= 0.72;

  // 延音踏板：琴键松开时若踏板踩下，则不收尾，直到踏板松开（或采样自然结束）才释放
  let sustain = false;
  let releaseAbs = start + hold;
  if (isMidiScore() && player.pedalEvents.length) {
    const keyUpSongSec = onsetSongSec + hold;
    if (pedalDownAt(keyUpSongSec)) {
      sustain = true;
      const releaseSongSec = Math.min(nextPedalRelease(keyUpSongSec), onsetSongSec + natural);
      releaseAbs = player.startTime + releaseSongSec;
    }
  }
  const end = Math.min(start + natural + 0.08, releaseAbs + release + 0.16);

  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.linearRampToValueAtTime(peak, start + attack);
  gain.gain.setTargetAtTime(peak * 0.7, start + attack, 0.3);
  if (sustain) {
    gain.gain.setTargetAtTime(0.0001, releaseAbs, release / 3.2);
  } else {
    gain.gain.setTargetAtTime(0.0001, start + hold, release / 3.2);
  }

  if (soft) {
    const softFilter = ctx.createBiquadFilter();
    softFilter.type = "lowpass";
    softFilter.frequency.value = 2600;
    softFilter.Q.value = 0.5;
    source.connect(softFilter).connect(gain).connect(outputFor(ctx, panForEvent(staff)));
  } else {
    source.connect(gain).connect(outputFor(ctx, panForEvent(staff)));
  }
  source.start(start);
  source.stop(end);
  player.activeNotes.set(key, { source, gain });
  source.onended = () => {
    const active = player.activeNotes.get(key);
    if (active && active.source === source) player.activeNotes.delete(key);
  };
  player.nodes.push(source);
}

function scheduleTone(ctx, start, duration, midi, waveform, velocity, staff, startBeat) {
  const gain = ctx.createGain();
  let peak = noteGainPeak(midi, velocity, staff, startBeat) * 0.72;
  const onsetSongSec = (startBeat || 0) / beatsPerSecond();

  // 软踏板（una corda）：踩下时音量略降、加低通柔化
  const soft = isMidiScore() && player.softPedalEvents.length && softPedalDownAt(onsetSongSec);
  if (soft) peak *= 0.72;

  const attack = clampNumber(duration * 0.08, 0.006, 0.018);
  const sustain = Math.max(attack + 0.01, duration);
  const release = clampNumber(duration * 0.45, 0.14, 0.6);
  const end = start + sustain + release + 0.04;

  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.linearRampToValueAtTime(peak, start + attack);
  gain.gain.exponentialRampToValueAtTime(peak * 0.72, start + sustain);
  gain.gain.exponentialRampToValueAtTime(0.0001, end);

  const filter = ctx.createBiquadFilter();
  filter.type = "lowpass";
  filter.frequency.value = soft ? 2600 : (waveform === "piano" ? 4200 : 5200);
  filter.Q.value = soft ? 0.5 : 0.7;
  gain.connect(filter).connect(outputFor(ctx, panForEvent(staff)));

  const harmonics = waveform === "piano" ? [[1, 1], [2, 0.32], [3, 0.12]] : [[1, 1]];
  harmonics.forEach(([multiple, level]) => {
    const oscillator = ctx.createOscillator();
    oscillator.type = waveform === "piano" ? "triangle" : waveform;
    oscillator.frequency.setValueAtTime(midiToFrequency(midi) * multiple, start);
    const partialGain = ctx.createGain();
    partialGain.gain.value = level;
    oscillator.connect(partialGain).connect(gain);
    oscillator.start(start);
    oscillator.stop(end + 0.02);
    player.nodes.push(oscillator);
  });
}

const SOLFEGE = {
  1: { formants: [[500, 1.0], [900, 0.55], [2500, 0.18]], onset: "d" },
  2: { formants: [[520, 1.0], [1750, 0.6], [2500, 0.2]], onset: "r" },
  3: { formants: [[300, 1.0], [2300, 0.65], [3000, 0.2]], onset: "m" },
  4: { formants: [[800, 1.0], [1200, 0.6], [2800, 0.2]], onset: "f" },
  5: { formants: [[500, 1.0], [900, 0.55], [2500, 0.18]], onset: "s" },
  6: { formants: [[800, 1.0], [1200, 0.6], [2800, 0.2]], onset: "l" },
  7: { formants: [[300, 1.0], [2300, 0.65], [3000, 0.2]], onset: "s" },
};

function scheduleConsonant(ctx, start, onset, peak) {
  const source = ctx.createBufferSource();
  source.buffer = noiseBuffer(ctx);
  const filter = ctx.createBiquadFilter();
  if (onset === "m") {
    filter.type = "lowpass";
    filter.frequency.value = 700;
  } else if (onset === "f") {
    filter.type = "highpass";
    filter.frequency.value = 3500;
  } else if (onset === "s") {
    filter.type = "highpass";
    filter.frequency.value = 5000;
  } else {
    filter.type = "bandpass";
    filter.frequency.value = 2200;
    filter.Q.value = 2;
  }
  const gain = ctx.createGain();
  const level = peak * (onset === "m" ? 0.5 : 0.32);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.linearRampToValueAtTime(level, start + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.055);
  source.connect(filter).connect(gain).connect(outputFor(ctx, 0));
  source.start(start);
  source.stop(start + 0.08);
  player.nodes.push(source);
}

function scheduleSolfege(ctx, start, duration, midi, velocity, degree) {
  const syllable = SOLFEGE[degree] || SOLFEGE[1];
  const frequency = midiToFrequency(midi);
  const peak = Math.max(0.0001, 0.17 * (0.6 + 0.4 * (velocity || 0.8)));
  const vowelStart = start + 0.04;
  const vocalDuration = Math.max(0.08, duration - 0.04);
  const release = Math.min(0.18, vocalDuration * 0.4);
  const output = ctx.createGain();
  output.gain.setValueAtTime(0.0001, vowelStart);
  output.gain.linearRampToValueAtTime(peak, vowelStart + Math.min(0.09, vocalDuration * 0.3));
  output.gain.setValueAtTime(peak * 0.82, vowelStart + Math.max(0.09, vocalDuration - release));
  output.gain.linearRampToValueAtTime(0.0001, vowelStart + vocalDuration + release);
  const lowpass = ctx.createBiquadFilter();
  lowpass.type = "lowpass";
  lowpass.frequency.value = 3600;
  lowpass.connect(output);
  output.connect(outputFor(ctx, 0));
  const vibrato = ctx.createOscillator();
  vibrato.frequency.value = 5.4;
  const vibratoGain = ctx.createGain();
  vibratoGain.gain.value = 4.5;
  vibrato.connect(vibratoGain);
  syllable.formants.forEach(([formant, level]) => {
    const source = ctx.createOscillator();
    source.type = "sawtooth";
    source.frequency.setValueAtTime(frequency * 0.985, vowelStart);
    source.frequency.linearRampToValueAtTime(frequency, vowelStart + 0.06);
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = formant;
    filter.Q.value = 9;
    const gain = ctx.createGain();
    gain.gain.value = level;
    source.connect(filter).connect(gain).connect(lowpass);
    vibratoGain.connect(source.frequency);
    source.start(vowelStart);
    source.stop(vowelStart + vocalDuration + release + 0.05);
    player.nodes.push(source);
  });
  vibrato.start(vowelStart);
  vibrato.stop(vowelStart + vocalDuration + release + 0.05);
  player.nodes.push(vibrato);
  scheduleConsonant(ctx, start, syllable.onset, peak);
}

function scheduleEvent(event, when, duration) {
  const ctx = player.context;
  const velocity = player.velocities.get(event) || Number(event.velocity) || 0.8;
  const staff = Number(event.staff) || 0;
  const startBeat = event.start_beat || 0;
  const mode = $("modeSelect").value;
  if (mode === "voice") {
    const raw = midiToDegree(event.midi, state.score.tonic, state.score.mode).degree;
    scheduleSolfege(ctx, when, duration, event.midi, velocity, Math.min(7, Math.max(1, raw)));
    return;
  }
  if (nearestSample(event.midi, velocity)) {
    scheduleSample(ctx, when, duration, event.midi, velocity, staff, startBeat);
  } else {
    scheduleTone(ctx, when, duration, event.midi, "piano", velocity, staff, startBeat);
  }
}

function beatsPerSecond() {
  return (state.score && state.score.tempo || 120) / 60 * player.speed;
}

function isSounding(event, beat) {
  return Boolean(event) && beat >= event.start_beat && beat < event.start_beat + event.beats;
}

function fmtTime(seconds) {
  const value = Math.max(0, seconds || 0);
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
}

function recolorOsmdCursor() {
  const host = $("staffView");
  const canvas = document.createElement("canvas");
  canvas.width = 30;
  canvas.height = 1;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.fillStyle = "#1971c2";
  context.fillRect(0, 0, 30, 1);
  const source = canvas.toDataURL("image/png");
  host.querySelectorAll('img[id^="cursorImg-"]').forEach((image) => {
    image.src = source;
    image.style.opacity = "0.9";
  });
}

function setOsmdNodeActive(node, active) {
  if (!node) return;
  const targets = [node, ...Array.from(node.querySelectorAll("path, use, rect, ellipse, circle"))];
  targets.forEach((element) => {
    if (active) {
      if (!player.osmdStyles.has(element)) {
        player.osmdStyles.set(element, { fill: element.style.fill, stroke: element.style.stroke });
      }
      element.style.fill = "#1971c2";
      element.style.stroke = "#1864ab";
    } else {
      const original = player.osmdStyles.get(element);
      if (original) {
        element.style.fill = original.fill;
        element.style.stroke = original.stroke;
        player.osmdStyles.delete(element);
      }
    }
  });
  if (node.classList) node.classList.toggle("is-active", active);
}

function clearOsmdHighlights() {
  player.osmdNoteNodes.forEach(({ node }) => setOsmdNodeActive(node, false));
  player.osmdHighlights.forEach((node) => setOsmdNodeActive(node, false));
  player.osmdHighlights = [];
}

function buildOsmdCursorMap() {
  const cursor = player.osmdCursor;
  if (!cursor || typeof cursor.GNotesUnderCursor !== "function") {
    player.osmdCursorMap = [];
    return;
  }
  const map = [];
  cursor.reset();
  let guard = 0;
  while (cursor.Iterator && !cursor.Iterator.EndReached && guard < 5000) {
    const stamp = cursor.Iterator.CurrentSourceTimestamp;
    const time = stamp && typeof stamp.RealValue === "number" ? stamp.RealValue : 0;
    const notes = cursor.GNotesUnderCursor() || [];
    const nodes = notes
      .map((gNote) => (typeof gNote.getSVGGElement === "function" ? gNote.getSVGGElement() : null))
      .filter((node) => node && node.classList);
    if (nodes.length) map.push({ time, nodes });
    cursor.next();
    guard += 1;
  }
  cursor.reset();
  player.osmdCursorMap = map;
}

function osmdGroupAtBeat(beat) {
  const time = Math.max(0, beat) / 4;
  let active = null;
  for (let i = 0; i < player.osmdCursorMap.length; i += 1) {
    const group = player.osmdCursorMap[i];
    if (group.time <= time + 0.008) active = group;
    else break;
  }
  return active;
}

function highlightOsmdByBeat(beat) {
  const active = osmdGroupAtBeat(beat);
  player.osmdHighlights.forEach((node) => setOsmdNodeActive(node, false));
  player.osmdHighlights = [];
  if (!active) return;
  active.nodes.forEach((node) => {
    setOsmdNodeActive(node, true);
    player.osmdHighlights.push(node);
  });
}

function highlightOsmdNotesAt(beat) {
  player.osmdNoteNodes.forEach(({ node, event }) => {
    setOsmdNodeActive(node, isSounding(event, beat));
  });
}

function updateOsmdCursor(beat) {
  const cursor = player.osmdCursor;
  if (!cursor) return;
  const iterator = cursor.Iterator;
  if (!iterator) return;
  const timestampValue = () => {
    const stamp = iterator.CurrentSourceTimestamp || iterator.currentTimeStamp;
    if (!stamp) return 0;
    return typeof stamp.RealValue === "number" ? stamp.RealValue : Number(stamp) || 0;
  };
  // 光标条停在"正在发声的音符"的起点上（与音符高亮同一目标），
  // 避免按 beat/4 推进时冲到下一个音符，造成高亮跑在演奏前面
  const group = osmdGroupAtBeat(beat);
  const target = group ? group.time : Math.max(0, beat) / 4;
  let guard = 0;
  while (!iterator.EndReached && timestampValue() < target - 0.0001 && guard < 2000) {
    cursor.next();
    guard += 1;
  }
  cursor.update();
  recolorOsmdCursor();
}

function updatePlayVisual() {
  const rate = beatsPerSecond();
  const total = Math.max(0.01, player.durationBeats);
  $("progress").style.width = Math.min(100, player.beat / total * 100).toFixed(1) + "%";
  $("currentTime").textContent = fmtTime(player.beat / rate);
  $("totalTime").textContent = fmtTime(total / rate);

  if (player.osmd) {
    if (player.osmdCursor && typeof player.osmdCursor.GNotesUnderCursor === "function"
        && player.osmdCursorMap.length) {
      // 高亮走预建时间映射，不显示 OSMD 播放光标条（rect 色块）
      highlightOsmdByBeat(player.beat);
    } else {
      highlightOsmdNotesAt(player.beat);
    }
  } else {
    document.querySelectorAll("#staffView .staff-note").forEach((node) => {
      const event = state.score.events[Number(node.dataset.noteIdx)];
      node.classList.toggle("active", isSounding(event, player.beat));
    });
    document.querySelectorAll("#staffView .staff-highlight").forEach((node) => {
      const event = state.score.events[Number(node.dataset.noteIdx)];
      node.setAttribute("opacity", isSounding(event, player.beat) ? 0.42 : 0);
    });
  }
  if (player.jpRenderers.length) highlightJianpu(player.beat);

  const lit = new Set();
  player.sorted.forEach((event) => {
    if (isSounding(event, player.beat)) lit.add(event.midi);
  });
  player.keys.forEach((node, midi) => node.classList.toggle("active", lit.has(midi)));
}

function tickPlayback() {
  if (player.status !== "playing" || !player.context) return;
  const now = player.context.currentTime;
  const rate = beatsPerSecond();
  player.beat = Math.max(0, (now - player.startTime) * rate);
  const horizon = player.beat + LOOKAHEAD * rate;
  while (player.next < player.sorted.length && player.sorted[player.next].start_beat <= horizon) {
    const index = player.next;
    const event = player.sorted[player.next++];
    const beatKey = event.start_beat || 0;
    const idealWhen = player.startTime + beatKey / rate;
    if (!player.groupWhen.has(beatKey)) {
      player.groupWhen.set(beatKey, idealWhen + humanizeOffset(index, event));
    }
    const when = Math.max(now + 0.004, player.groupWhen.get(beatKey));
    const noteSeconds = articulatedSeconds(event, rate);
    scheduleEvent(event, when, noteSeconds);
  }
  updatePlayVisual();
  if (player.beat > player.durationBeats + 0.8) stopPlayback();
}

function stopPlayback() {
  if (player.timer) clearInterval(player.timer);
  player.timer = null;
  player.nodes.forEach((node) => {
    try { node.stop(); } catch (_) { /* 已结束 */ }
    try { node.disconnect(); } catch (_) { /* 已断开 */ }
  });
  player.nodes = [];
  player.activeNotes.clear();
  clearOsmdHighlights();
  clearJianpuHighlight();
  player.status = "stopped";
  player.beat = 0;
  player.startTime = 0;
  player.next = 0;
  if (player.context && player.context.state === "suspended") player.context.resume().catch(() => {});
  $("playBtn").textContent = "▶";
  $("progress").style.width = "0%";
  $("currentTime").textContent = "0:00";
  document.querySelectorAll(".staff-note.active, .jp-note.active").forEach((node) => node.classList.remove("active"));
  document.querySelectorAll(".staff-highlight").forEach((node) => node.setAttribute("opacity", 0));
  player.keys.forEach((node) => node.classList.remove("active"));
  if (player.osmdCursor) {
    player.osmdCursor.reset();
    player.osmdCursor.hide();
  }
}

async function startPlayback() {
  if (!state.score) {
    toast("请先打开一首曲谱", "error");
    return;
  }
  if ($("modeSelect").value === "voice" && $("partSelect").value === "all") {
    $("partSelect").value = "0";
    toast("唱谱模式按单声部演唱，已切换到右手");
  }
  const events = playableEvents();
  if (!events.length) {
    toast("当前声部没有可演奏的音符", "error");
    return;
  }
  try {
    const ctx = ensureAudio();
    player.sorted = [...events].sort((a, b) => a.start_beat - b.start_beat || (a.staff || 0) - (b.staff || 0));
    player.durationBeats = Math.max(...events.map((event) => event.start_beat + event.beats));
    player.next = 0;
    player.beat = 0;
    player.speed = 1;
    player.velocities = isMidiScore()
      ? new Map(player.sorted.map((event) => [event, clampNumber(Number(event.velocity) || 0.7, 0.2, 1)]))
      : buildExpressiveVelocities(player.sorted);
    player.pedalEvents = (state.score && state.score.pedal || [])
      .slice().sort((a, b) => a.time - b.time);
    player.softPedalEvents = (state.score && state.score.soft_pedal || [])
      .slice().sort((a, b) => a.time - b.time);
    player.groupWhen.clear();
    if ($("modeSelect").value === "piano") {
      await loadSalamanderSamples(ctx, events.map((event) => event.midi));
      if (!hasSalamanderSamples()) await loadLocalSamples(ctx, events.map((event) => event.midi));
    }
    if (!player.sourceShown) {
      toast(hasSalamanderSamples() ? "Salamander 双层采样已启用" : "Salamander 不可用，使用本地 WAV 采样", "success");
      player.sourceShown = true;
    }
    if (player.osmdCursor) {
      player.osmdCursor.reset();
      player.osmdCursor.hide();
    }
    player.startTime = ctx.currentTime + 0.08;
    player.lastTime = player.startTime;
    player.status = "playing";
    player.timer = setInterval(tickPlayback, 25);
    $("playBtn").textContent = "⏸";
    $("totalTime").textContent = fmtTime(player.durationBeats / beatsPerSecond());
    tickPlayback();
  } catch (error) {
    toast("演奏失败：" + error.message, "error");
  }
}

async function togglePlayback() {
  if (player.status === "playing") {
    if (player.timer) clearInterval(player.timer);
    player.timer = null;
    await player.context.suspend();
    player.status = "paused";
    $("playBtn").textContent = "▶";
    return;
  }
  if (player.status === "paused") {
    await player.context.resume();
    player.lastTime = player.context.currentTime;
    player.status = "playing";
    player.timer = setInterval(tickPlayback, 25);
    $("playBtn").textContent = "⏸";
    return;
  }
  await startPlayback();
}

function tempoInputHtml(tempo) {
  const value = clampNumber(Math.round(Number(tempo) || 120), 20, 300);
  return `♩=<input class="tempo-input" data-tempo-input type="number" min="20" max="300" step="1" value="${value}" title="点击修改演奏速度（每分钟拍数）" aria-label="演奏速度">`;
}

function renderScoreMeta() {
  const score = state.score;
  const title = score.title || "未命名曲谱";
  const parts = [
    tempoInputHtml(score.tempo),
    `1=${score.tonic || "C"}${isMinor(score.mode) ? " 小调" : ""}`,
    `${score.beats_per_measure || 4}/${score.beat_unit || 4}`,
    `${(score.events || []).filter(hasMidi).length} 个音符`,
  ];
  $("practiceTitle").textContent = title;
  $("practiceMeta").innerHTML = parts.join(" · ");
  $("paperTitle").textContent = title;
  $("paperMeta").innerHTML = parts.join(" · ");
}

function applyTempo(value) {
  const next = clampNumber(Math.round(Number(value) || 120), 20, 300);
  if (!state.score) return;
  const oldRate = beatsPerSecond();
  const active = player.context && (player.status === "playing" || player.status === "paused");
  const elapsedBeats = active
    ? (player.status === "paused" ? player.beat : Math.max(0, (player.context.currentTime - player.startTime) * oldRate))
    : player.beat;
  const scheduledLimit = active ? elapsedBeats + LOOKAHEAD * oldRate : elapsedBeats;
  state.score.tempo = next;
  if (active) {
    const newRate = beatsPerSecond();
    player.startTime = player.context.currentTime - elapsedBeats / newRate;
    player.beat = elapsedBeats;
    const nextIndex = player.sorted.findIndex((event) => event.start_beat > scheduledLimit + 0.001);
    player.next = nextIndex === -1 ? player.sorted.length : nextIndex;
    player.groupWhen.clear();
  }
  document.querySelectorAll("[data-tempo-input]").forEach((input) => {
    if (String(next) !== input.value) input.value = String(next);
  });
  $("totalTime").textContent = fmtTime(player.durationBeats / beatsPerSecond());
  if (player.status === "playing") updatePlayVisual();
}

function renderCurrentScore() {
  stopPlayback();
  renderScoreMeta();
  renderStaff(state.score);
  renderJianpu(state.score);
  buildKeyboard(state.score);
  $("totalTime").textContent = fmtTime(
    Math.max(0, ...(state.score.events || []).map((event) => event.start_beat + event.beats))
    / beatsPerSecond());
}

function enterPractice() {
  document.querySelector("main").hidden = true;
  document.body.classList.add("practicing");
  $("practice").hidden = false;
  $("playerDock").hidden = false;
  renderCurrentScore();
  window.scrollTo(0, 0);
}

function backToLibrary() {
  stopPlayback();
  $("practice").hidden = true;
  $("playerDock").hidden = true;
  document.querySelector("main").hidden = false;
  document.body.classList.remove("practicing");
  window.scrollTo(0, 0);
  runSearch();
}

function selectedProviders() {
  const order = [
    ["pLocal", "local"], ["pOpenScore", "openscore"], ["pMutopia", "mutopia"],
    ["pImslp", "imslp"], ["pMusopen", "musopen"], ["pPianoMidi", "pianomidi"],
  ];
  const items = order.filter(([id]) => $(id) && $(id).checked).map(([, name]) => name);
  return items.length ? items : ["local"];
}

async function runSearch() {
  const query = $("searchInput").value.trim();
  const params = new URLSearchParams({
    q: query,
    providers: selectedProviders().join(","),
    limit: "16",
  });
  try {
    const response = await fetch("/api/search?" + params.toString());
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "搜索失败");
    state.results = data.items || [];
    renderGrid();
    $("scoreCount").textContent = query
      ? `“${query}” · ${state.results.length} 条结果`
      : `${state.results.length} 首可练习`;
    if (query && !state.results.length) toast("没有找到，换个关键词或导入自己的曲谱");
  } catch (error) {
    toast("搜索失败：" + error.message, "error");
  }
}

function renderGrid() {
  const host = $("scoreGrid");
  host.innerHTML = "";
  if (!state.results.length) {
    const empty = document.createElement("div");
    empty.className = "score-empty";
    empty.textContent = "没有找到曲谱。换个关键词，或上传你自己的 MusicXML。";
    host.appendChild(empty);
    return;
  }
  state.results.forEach((item) => {
    const card = document.createElement("article");
    card.className = "score-card";
    const title = document.createElement("h3");
    title.textContent = item.title || "未命名曲谱";
    const meta = document.createElement("p");
    const external = item.kind === "link" || !item.key;
    meta.textContent = [
      item.source === "user" ? "我的曲谱" : (item.source || "曲谱库"),
      item.composer,
      item.tonic && "1=" + item.tonic,
      item.meter,
      item.tempo && "♩=" + item.tempo,
    ].filter(Boolean).join(" · ");
    card.append(title, meta);
    if (item.tags && item.tags.length) {
      const tags = document.createElement("div");
      tags.className = "tags";
      item.tags.slice(0, 4).forEach((tag) => {
        const chip = document.createElement("span");
        chip.className = "tag";
        chip.textContent = tag;
        tags.appendChild(chip);
      });
      card.appendChild(tags);
    }
    const open = document.createElement("div");
    open.className = "open";
    open.textContent = external ? "打开来源 ↗" : "看谱演奏 →";
    card.appendChild(open);
    card.addEventListener("click", () => {
      if (external) {
        if (item.url) window.open(item.url, "_blank", "noopener");
        else toast("该结果没有可打开的链接", "error");
      } else {
        openScore(item.key);
      }
    });
    host.appendChild(card);
  });
}

async function openScore(key) {
  toast("正在载入曲谱…");
  try {
    const response = await fetch("/api/scores/" + encodeURIComponent(key));
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "载入失败");
    state.score = data;
    state.id = data.id || null;
    state.key = key;
    enterPractice();
    toast(`已打开《${data.title}》，点播放跟着弹`);
  } catch (error) {
    toast("载入失败：" + error.message, "error");
  }
}

async function importScore(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  toast("正在导入曲谱…");
  try {
    const response = await fetch("/api/scores/import", { method: "POST", body: form });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "导入失败");
    await runSearch();
    await openScore(data.key);
  } catch (error) {
    toast("导入失败：" + error.message, "error");
  }
}

function bindEvents() {
  $("searchBtn").addEventListener("click", runSearch);
  $("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") runSearch();
  });
  document.querySelectorAll("#quickTags .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      $("searchInput").value = chip.dataset.query || "";
      runSearch();
    });
  });
  document.querySelectorAll("#providerRow input").forEach((input) => {
    input.addEventListener("change", runSearch);
  });

  $("uploadBtn").addEventListener("click", () => $("uploadInput").click());
  $("uploadInput").addEventListener("change", () => {
    importScore($("uploadInput").files[0]);
    $("uploadInput").value = "";
  });
  document.querySelectorAll("[data-soon]").forEach((button) => {
    button.addEventListener("click", () => toast(`${button.dataset.soon}将在后续版本开放`));
  });

  $("backBtn").addEventListener("click", backToLibrary);
  $("downloadXmlBtn").addEventListener("click", () => {
    if (!state.id) return toast("尚无乐谱可下载", "error");
    window.location.href = `/api/score/${encodeURIComponent(state.id)}/musicxml`;
  });
  $("downloadPdfBtn").addEventListener("click", () => {
    if (!state.id) return toast("尚无乐谱可导出 PDF", "error");
    window.location.href = `/api/score/${encodeURIComponent(state.id)}/pdf`;
  });

  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      $(tab.dataset.tab + "Panel").classList.add("active");
    });
  });

  $("partSelect").addEventListener("change", () => {
    stopPlayback();
    if ($("modeSelect").value === "voice") $("partSelect").value = "0";
  });
  $("modeSelect").addEventListener("change", stopPlayback);
  $("playBtn").addEventListener("click", togglePlayback);
  $("stopBtn").addEventListener("click", stopPlayback);
  document.addEventListener("change", (event) => {
    if (event.target && event.target.matches && event.target.matches("[data-tempo-input]")) {
      applyTempo(event.target.value);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target && event.target.matches
        && event.target.matches("[data-tempo-input]")) {
      event.preventDefault();
      applyTempo(event.target.value);
      event.target.blur();
    }
  });
  $("volumeInput").addEventListener("input", () => {
    if (player.master && player.context) {
      player.master.gain.setTargetAtTime(
        Number($("volumeInput").value) / 17, player.context.currentTime, 0.02);
    }
  });

  window.addEventListener("beforeunload", stopPlayback);
}

bindEvents();
runSearch();
