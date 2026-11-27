/**
 * 自研钢琴简谱 SVG 排版与渲染引擎（JianpuSvgEngine）
 *
 * 特点：
 * 1. 纯数学网格排版，不依赖隐藏 DOM 的 getBBox()，彻底消除挤压与裁切；
 * 2. 支持双手大谱表（右手/左手按拍点纵向对齐）及柱式和弦纵向叠排；
 * 3. 按真实小节宽度自适应折行，行内小节线整齐对齐；
 * 4. 正确支持大小调首调映射、休止符、附点、增减时线（同拍共线）与延音线；
 * 5. 支持分帧渐进渲染大曲谱，以及和弦/双手同步高亮与自动滚动跟随。
 */
(function (global) {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";
  const EPS = 1e-4;
  const STANDARD_DURATIONS = [4, 3, 2, 1.5, 1, 0.75, 0.5, 0.375, 0.25, 0.1875, 0.125, 0.0625];

  function svgEl(tag, attrs, text) {
    const el = document.createElementNS(SVG_NS, tag);
    if (attrs) {
      for (const key in attrs) {
        if (attrs[key] !== undefined && attrs[key] !== null) {
          el.setAttribute(key, String(attrs[key]));
        }
      }
    }
    if (text !== undefined) el.textContent = String(text);
    return el;
  }

  function clamp(v, min, max) {
    return Math.max(min, Math.min(max, v));
  }

  function snapGrid(value, grid) {
    return Math.round(value / grid) * grid;
  }

  /**
   * 计算给定拍号的每小节长度（以四分音符为 1 拍单位）。
   */
  function getMeasureQuarterLen(score) {
    const num = Math.max(1, Number(score.beats_per_measure) || 4);
    const den = Math.max(1, Number(score.beat_unit) || 4);
    return num * (4 / den);
  }

  /**
   * 把可能跨小节的事件切分到各自小节，保留对原始 NoteEvent 的引用用于高亮。
   */
  function splitEventsAtMeasures(events, measureLen) {
    const result = [];
    for (const ev of events || []) {
      const rawStart = Math.max(0, Number(ev.start_beat) || 0);
      const rawDur = Math.max(0.0625, Number(ev.beats) || 0.25);
      const rawEnd = rawStart + rawDur;
      let cursor = rawStart;
      while (cursor < rawEnd - EPS) {
        const m = Math.floor((cursor + EPS) / measureLen);
        const mEnd = (m + 1) * measureLen;
        const segEnd = Math.min(rawEnd, mEnd);
        const dur = segEnd - cursor;
        if (dur <= EPS) break;
        let tie = ev.tie || "";
        if (!tie) {
          if (cursor > rawStart + EPS) {
            tie = segEnd < rawEnd - EPS ? "continue" : "stop";
          } else if (segEnd < rawEnd - EPS) {
            tie = "start";
          }
        }
        result.push({
          midi: ev.midi,
          start_beat: cursor,
          beats: dur,
          measure: m,
          staff: Number(ev.staff) || 0,
          tie,
          _source: ev,
        });
        cursor = segEnd;
      }
    }
    return result;
  }

  /**
   * 规范化单个声部在一个小节内的事件：对齐网格、合并同拍和弦、截断重叠、补齐休止符。
   */
  function normalizeStaffMeasure(rawEvents, measureIndex, measureStart, measureLen, isMidi) {
    const grid = isMidi ? 0.125 : 0.0625;
    const cleaned = [];

    for (const ev of rawEvents) {
      let localStart = ev.start_beat - measureStart;
      if (isMidi) localStart = snapGrid(localStart, grid);
      localStart = clamp(localStart, 0, Math.max(0, measureLen - grid));
      const dur = Math.max(grid, isMidi ? snapGrid(ev.beats, grid) : ev.beats);
      cleaned.push({
        midi: ev.midi,
        localStart,
        beats: dur,
        tie: ev.tie || "",
        _source: ev._source,
      });
    }

    cleaned.sort((a, b) => a.localStart - b.localStart || ((b.midi ?? -1) - (a.midi ?? -1)));

    // 按局部起点分组为同时发声列（和弦或单音或休止符）
    const groups = [];
    for (const item of cleaned) {
      const last = groups[groups.length - 1];
      if (last && Math.abs(last.localStart - item.localStart) < 1e-3) {
        last.items.push(item);
      } else {
        groups.push({ localStart: item.localStart, items: [item] });
      }
    }

    const timeline = [];
    let cursor = 0;

    const pushRest = (start, dur) => {
      if (dur < 0.0625 - EPS) return;
      timeline.push({ localStart: start, dur, notes: [], isRest: true });
    };

    for (let i = 0; i < groups.length; i += 1) {
      const grp = groups[i];
      const start = grp.localStart;
      const nextStart = i + 1 < groups.length ? groups[i + 1].localStart : measureLen;

      if (start > cursor + 0.0625 - EPS) {
        pushRest(cursor, start - cursor);
      } else if (start < cursor - EPS) {
        // 避免前一组略微越过当前起点
      }

      const pitched = grp.items.filter((x) => x.midi !== null && x.midi !== undefined);
      const maxSpan = Math.max(grid, nextStart - start);

      if (pitched.length === 0) {
        // 纯休止符组
        const restDur = Math.min(maxSpan, Math.max(...grp.items.map((x) => x.beats)));
        pushRest(start, restDur);
        cursor = Math.max(cursor, start + restDur);
        continue;
      }

      // MIDI 断奏导致的微小间隙（<= 0.25 拍且不是句尾休止）自动吸附到下一音起点，避免满屏碎休止符
      let rawMaxDur = Math.max(...pitched.map((x) => x.beats));
      if (isMidi && i + 1 < groups.length) {
        const gap = maxSpan - rawMaxDur;
        if (gap > 0 && gap <= 0.25 + EPS && rawMaxDur >= maxSpan * 0.45) {
          rawMaxDur = maxSpan;
        }
      }

      const colDur = Math.min(maxSpan, Math.max(grid, rawMaxDur));
      const reachesBarEnd = start + colDur >= measureLen - EPS;
      const atBarStart = start <= EPS;
      // 按音高从高到低排序，使和弦从上到下符合音高直觉，同一 MIDI 去重
      const seenMidi = new Set();
      const sortedNotes = [];
      pitched.sort((a, b) => b.midi - a.midi);
      for (const n of pitched) {
        if (seenMidi.has(n.midi)) continue;
        seenMidi.add(n.midi);
        let effTie = n.tie || "";
        if (effTie === "start" && !reachesBarEnd) effTie = "";
        else if (effTie === "stop" && !atBarStart) effTie = "";
        else if (effTie === "continue") {
          if (!atBarStart && !reachesBarEnd) effTie = "";
          else if (!atBarStart) effTie = "start";
          else if (!reachesBarEnd) effTie = "stop";
        }
        sortedNotes.push({ ...n, tie: effTie });
      }

      timeline.push({
        localStart: start,
        dur: colDur,
        notes: sortedNotes,
        isRest: false,
      });
      cursor = Math.max(cursor, start + colDur);
    }

    if (measureLen - cursor > 0.0625 - EPS) {
      pushRest(cursor, measureLen - cursor);
    }
    return timeline;
  }

  /**
   * 将时值分解为标准简谱渲染单元（单个音符/休止符可带减时线、附点或增时线）。
   */
  function decomposeDuration(dur, isRest) {
    const tokens = [];
    let rem = dur;

    if (isRest) {
      // 休止符按拍写 0：整拍写多个 0，不足一拍用减时线或附点
      while (rem >= 1 - EPS) {
        tokens.push({ dur: 1, dashes: 0, dots: 0, underlines: 0, tieFromPrev: false, tieToNext: false });
        rem -= 1;
      }
      let guard = 0;
      while (rem >= 0.0625 - EPS && guard < 6) {
        guard += 1;
        const pick = pickRestToken(rem);
        tokens.push({ dur: pick.dur, dashes: 0, dots: pick.dots, underlines: pick.underlines, tieFromPrev: false, tieToNext: false });
        rem -= pick.dur;
      }
      return tokens;
    }

    let guard = 0;
    while (rem >= 0.0625 - EPS && guard < 6) {
      guard += 1;
      const pick = pickNoteToken(rem);
      tokens.push({
        dur: pick.dur,
        dashes: pick.dashes,
        dots: pick.dots,
        underlines: pick.underlines,
        tieFromPrev: tokens.length > 0,
        tieToNext: false,
      });
      rem -= pick.dur;
    }
    for (let i = 0; i < tokens.length - 1; i += 1) {
      tokens[i].tieToNext = true;
    }
    return tokens;
  }

  function pickNoteToken(rem) {
    // 标准单符号时值表（含 4拍/3拍/2拍/1.5拍/1.75拍 等）
    const table = [
      { dur: 4, dashes: 3, dots: 0, underlines: 0 },
      { dur: 3, dashes: 2, dots: 0, underlines: 0 },
      { dur: 2, dashes: 1, dots: 0, underlines: 0 },
      { dur: 1.75, dashes: 0, dots: 2, underlines: 0 },
      { dur: 1.5, dashes: 0, dots: 1, underlines: 0 },
      { dur: 1, dashes: 0, dots: 0, underlines: 0 },
      { dur: 0.75, dashes: 0, dots: 1, underlines: 1 },
      { dur: 0.5, dashes: 0, dots: 0, underlines: 1 },
      { dur: 0.375, dashes: 0, dots: 1, underlines: 2 },
      { dur: 0.25, dashes: 0, dots: 0, underlines: 2 },
      { dur: 0.1875, dashes: 0, dots: 1, underlines: 3 },
      { dur: 0.125, dashes: 0, dots: 0, underlines: 3 },
      { dur: 0.0625, dashes: 0, dots: 0, underlines: 4 },
    ];
    for (const row of table) {
      if (rem >= row.dur - EPS) return row;
    }
    return table[table.length - 1];
  }

  function pickRestToken(rem) {
    const table = [
      { dur: 0.75, dots: 1, underlines: 1 },
      { dur: 0.5, dots: 0, underlines: 1 },
      { dur: 0.375, dots: 1, underlines: 2 },
      { dur: 0.25, dots: 0, underlines: 2 },
      { dur: 0.1875, dots: 1, underlines: 3 },
      { dur: 0.125, dots: 0, underlines: 3 },
      { dur: 0.0625, dots: 0, underlines: 4 },
    ];
    for (const row of table) {
      if (rem >= row.dur - EPS) return row;
    }
    return table[table.length - 1];
  }

  /**
   * 展开单个声部小节时间线为细粒度槽位（每个增时线 '-' 与切分符均占用准确拍点）。
   */
  function expandStaffTimeline(timeline, tonic, mode, degreeFn) {
    const slotMap = new Map();
    for (const item of timeline) {
      const tokens = decomposeDuration(item.dur, item.isRest);
      let cursor = item.localStart;
      tokens.forEach((tok, tokIdx) => {
        const key = Number(cursor.toFixed(4));
        const isFirstTok = tokIdx === 0;
        const isLastTok = tokIdx === tokens.length - 1;

        if (item.isRest) {
          slotMap.set(key, {
            beatOffset: key,
            kind: "rest",
            underlines: tok.underlines,
            dots: tok.dots,
            notes: [{ degree: "0", acc: 0, oct: 0, _source: null }],
            tieStart: false,
            tieEnd: false,
          });
        } else {
          const mappedNotes = item.notes.map((n) => {
            const deg = degreeFn(n.midi, tonic, mode);
            return {
              degree: String(deg.degree),
              acc: deg.acc,
              oct: deg.oct,
              midi: n.midi,
              _source: n._source,
            };
          });
          const hasExtTieStart = isLastTok && item.notes.some((n) => n.tie === "start" || n.tie === "continue");
          const hasExtTieEnd = isFirstTok && item.notes.some((n) => n.tie === "stop" || n.tie === "continue");
          slotMap.set(key, {
            beatOffset: key,
            kind: "note",
            underlines: tok.underlines,
            dots: tok.dots,
            notes: mappedNotes,
            tieStart: tok.tieToNext || hasExtTieStart,
            tieEnd: tok.tieFromPrev || hasExtTieEnd,
          });
        }

        // 增时线 '-' 占位（每 1 拍一个）
        for (let d = 1; d <= tok.dashes; d += 1) {
          const dashKey = Number((cursor + d).toFixed(4));
          if (!slotMap.has(dashKey)) {
            slotMap.set(dashKey, {
              beatOffset: dashKey,
              kind: "dash",
              underlines: 0,
              dots: 0,
              notes: [],
              tieStart: false,
              tieEnd: false,
            });
          }
        }
        cursor += tok.dur;
      });
    }
    return slotMap;
  }

  /**
   * 构建一小节的排版数据，计算每个时间槽位的最小安全宽度与垂直高度。
   */
  function buildMeasureLayout(measureIndex, measureStart, measureLen, eventsByStaff, staves, score, degreeFn) {
    const isMidi = score.source === "midi";
    const tonic = score.tonic || "C";
    const mode = score.mode || "major";
    const staffMaps = new Map();
    const allBeatKeys = new Set();

    for (const staff of staves) {
      const raw = eventsByStaff.get(staff) || [];
      const timeline = normalizeStaffMeasure(raw, measureIndex, measureStart, measureLen, isMidi);
      const slotMap = expandStaffTimeline(timeline, tonic, mode, degreeFn);
      staffMaps.set(staff, slotMap);
      for (const k of slotMap.keys()) allBeatKeys.add(k);
    }

    const sortedBeats = Array.from(allBeatKeys).sort((a, b) => a - b);
    const columns = sortedBeats.map((beatOffset) => {
      let leftPad = 2;
      let rightPad = 2;
      const cells = new Map();

      for (const staff of staves) {
        const cell = staffMaps.get(staff).get(beatOffset) || null;
        cells.set(staff, cell);
        if (cell && cell.kind !== "dash") {
          if (cell.notes.some((n) => n.acc !== 0)) leftPad = Math.max(leftPad, 12);
          if (cell.dots > 0) rightPad = Math.max(rightPad, 4 + cell.dots * 6);
        }
      }

      const bodyWidth = 16; // 数字或增时线中心占位宽度
      const gap = 8;
      return {
        beatOffset,
        leftPad,
        bodyWidth,
        rightPad,
        minWidth: leftPad + bodyWidth + rightPad + gap,
        cells,
      };
    });

    // 若整小节无任何事件（空小节），放置占位
    if (columns.length === 0) {
      const emptyCells = new Map();
      for (const staff of staves) {
        emptyCells.set(staff, {
          beatOffset: 0,
          kind: "rest",
          underlines: 0,
          dots: 0,
          notes: [{ degree: "0", acc: 0, oct: 0, _source: null }],
          tieStart: false,
          tieEnd: false,
        });
      }
      columns.push({
        beatOffset: 0,
        leftPad: 2,
        bodyWidth: 16,
        rightPad: 2,
        minWidth: 32,
        cells: emptyCells,
      });
    }

    const measurePadLeft = 12;
    const measurePadRight = 10;
    const colsMinSum = columns.reduce((acc, c) => acc + c.minWidth, 0);
    const minWidth = measurePadLeft + colsMinSum + measurePadRight;

    // 预计算每个声部在本小节所需的和弦垂直空间
    const staffMetrics = new Map();
    for (const staff of staves) {
      let maxTopAboveFirst = 14; // 含高位点与连音线
      let maxStackHeight = 20;   // 第一个数字中心到最后一个数字中心的距离 + 底部减时线/低音点
      for (const col of columns) {
        const cell = col.cells.get(staff);
        if (!cell || cell.kind === "dash") continue;
        const metric = measureCellVertical(cell);
        maxTopAboveFirst = Math.max(maxTopAboveFirst, metric.topExtent);
        maxStackHeight = Math.max(maxStackHeight, metric.stackBody + metric.bottomExtent);
      }
      staffMetrics.set(staff, { topExtent: maxTopAboveFirst, bottomAndBody: maxStackHeight });
    }

    return {
      measureIndex,
      measureLen,
      columns,
      minWidth,
      padLeft: measurePadLeft,
      padRight: measurePadRight,
      staffMetrics,
    };
  }

  /**
   * 计算单个音符/和弦列内部垂直布局，保证高低八度点与上下音符数字永不重叠。
   */
  function measureCellVertical(cell) {
    const notes = cell.notes || [];
    if (notes.length === 0) {
      return { topExtent: 14, stackBody: 0, bottomExtent: 16, yOffsets: [] };
    }
    const dotStep = 4.6;
    const topSpace = (n) => (n.oct > 0 ? 11 + Math.min(3, n.oct) * dotStep : 0);
    const innerBotSpace = (n) => (n.oct < 0 ? 10 + Math.min(3, -n.oct) * dotStep : 0);

    const yOffsets = [0];
    for (let i = 1; i < notes.length; i += 1) {
      const prev = notes[i - 1];
      const curr = notes[i];
      const gap = 28 + innerBotSpace(prev) + topSpace(curr);
      yOffsets.push(yOffsets[i - 1] + gap);
    }

    const last = notes[notes.length - 1];
    const underlineH = cell.underlines > 0 ? 12 + cell.underlines * 4.2 : 11;
    const lowDotH = last.oct < 0 ? 5 + Math.min(3, -last.oct) * dotStep : 0;
    const tieExtra = cell.tieStart || cell.tieEnd ? 8 : 0;

    return {
      topExtent: 14 + topSpace(notes[0]) + tieExtra,
      stackBody: yOffsets[yOffsets.length - 1],
      bottomExtent: Math.max(15, underlineH + lowDotH + 4),
      yOffsets,
    };
  }

  /**
   * 将小节按容器可用宽度动态折行，避免强行挤压。
   */
  function packMeasuresIntoRows(measures, availableWidth, labelReserve) {
    const rows = [];
    const usable = Math.max(260, availableWidth - labelReserve);
    let current = [];
    let currentMin = 0;

    for (const m of measures) {
      const wouldExceedWidth = current.length > 0 && currentMin + m.minWidth > usable;
      const wouldExceedMaxBars = current.length >= 4;
      if (wouldExceedWidth || wouldExceedMaxBars) {
        rows.push({ measures: current, minWidth: currentMin });
        current = [];
        currentMin = 0;
      }
      current.push(m);
      currentMin += m.minWidth;
    }
    if (current.length) {
      rows.push({ measures: current, minWidth: currentMin });
    }
    return rows;
  }

  /**
   * 渲染曲首信息条（调号、调式、拍号、速度），与音符行分离，避免碰撞。
   */
  function renderHeaderSvg(score, width, isMinorFn) {
    const h = 34;
    const svg = svgEl("svg", {
      class: "jp-header-svg",
      width,
      height: h,
      viewBox: `0 0 ${width} ${h}`,
    });
    const tonic = score.tonic || "C";
    const minorText = isMinorFn(score.mode) ? " 小调" : "";
    const meterText = `${score.beats_per_measure || 4}/${score.beat_unit || 4}`;
    const tempoText = `♩=${Math.round(Number(score.tempo) || 120)}`;

    const keyLabel = svgEl("text", {
      x: 4, y: 23, "font-size": 16, "font-weight": 700, fill: "#1f2430",
    }, `1=${tonic}${minorText}`);
    const meterLabel = svgEl("text", {
      x: 108, y: 23, "font-size": 15, "font-weight": 600, fill: "#4b5563",
    }, meterText);
    const tempoLabel = svgEl("text", {
      x: 162, y: 23, "font-size": 14, fill: "#6b7280", "data-jp-tempo": "true",
    }, tempoText);

    svg.append(keyLabel, meterLabel, tempoLabel);
    return svg;
  }

  /**
   * 渲染单行简谱 SVG，并返回本行注册的可高亮音符列表。
   */
  function renderRowSvg(row, rowIdx, staves, availableWidth, labelReserve, beatUnitQuarters) {
    const usableTarget = Math.max(260, availableWidth - labelReserve);
    const contentWidth = Math.max(usableTarget, row.minWidth);
    const totalWidth = labelReserve + contentWidth;
    const extraSpace = Math.max(0, contentWidth - row.minWidth);

    // 算出本行各声部基线（取本行内所有小节的最大高度，保证左右对齐）
    const rowStaffLayout = new Map();
    let yCursor = 16;
    const staffGap = 16;

    for (let sIdx = 0; sIdx < staves.length; sIdx += 1) {
      const staff = staves[sIdx];
      let topExtent = 22;
      let bottomAndBody = 32;
      for (const m of row.measures) {
        const sm = m.staffMetrics.get(staff);
        if (sm) {
          topExtent = Math.max(topExtent, sm.topExtent);
          bottomAndBody = Math.max(bottomAndBody, sm.bottomAndBody);
        }
      }
      const baseY = yCursor + topExtent;
      rowStaffLayout.set(staff, {
        baseY,
        topY: yCursor,
        bottomY: baseY + bottomAndBody,
      });
      yCursor = baseY + bottomAndBody + (sIdx < staves.length - 1 ? staffGap : 14);
    }

    const totalHeight = Math.max(66, yCursor);
    const svg = svgEl("svg", {
      class: "jp-row-svg",
      width: totalWidth,
      height: totalHeight,
      viewBox: `0 0 ${totalWidth} ${totalHeight}`,
    });

    const firstLayout = rowStaffLayout.get(staves[0]);
    const lastLayout = rowStaffLayout.get(staves[staves.length - 1]);
    const barTop = firstLayout.baseY - 14;
    const barBottom = lastLayout.baseY + 18;

    // 左侧大谱表标签与连接括号
    if (staves.length > 1 && labelReserve > 0) {
      svg.appendChild(svgEl("text", {
        x: 4, y: firstLayout.baseY + 6, "font-size": 12, "font-weight": 700, fill: "#4f46e5",
      }, "右手"));
      svg.appendChild(svgEl("text", {
        x: 4, y: lastLayout.baseY + 6, "font-size": 12, "font-weight": 700, fill: "#6b7280",
      }, "左手"));
      // 左侧竖向连谱线
      svg.appendChild(svgEl("line", {
        x1: labelReserve - 8, y1: barTop,
        x2: labelReserve - 8, y2: barBottom,
        stroke: "#4f46e5", "stroke-width": 2,
      }));
      svg.appendChild(svgEl("line", {
        x1: labelReserve - 8, y1: barTop,
        x2: labelReserve - 2, y2: barTop,
        stroke: "#4f46e5", "stroke-width": 1.6,
      }));
      svg.appendChild(svgEl("line", {
        x1: labelReserve - 8, y1: barBottom,
        x2: labelReserve - 2, y2: barBottom,
        stroke: "#4f46e5", "stroke-width": 1.6,
      }));
    }

    const noteItems = [];
    const totalCols = row.measures.reduce((sum, m) => sum + m.columns.length, 0);
    const rawBonus = totalCols > 0 ? extraSpace / totalCols : 0;
    // 末行不足一整行小节数时限制最大拉伸步长，避免单小节被拉成横跨整页的畸形宽条
    const bonusPerCol = row.isLastRow && row.measures.length < 3
      ? Math.min(22, rawBonus)
      : rawBonus;

    let xCursor = labelReserve;
    // 行首起始小节线
    svg.appendChild(svgEl("line", {
      x1: xCursor, y1: barTop, x2: xCursor, y2: barBottom,
      stroke: "#374151", "stroke-width": 1.3,
    }));

    for (let mIdx = 0; mIdx < row.measures.length; mIdx += 1) {
      const m = row.measures[mIdx];
      const mStartX = xCursor;
      const mWidth = m.minWidth + bonusPerCol * m.columns.length;

      // 小节号（每行首小节或第 1 小节显示，浅灰色不干扰读谱）
      if (mIdx === 0) {
        svg.appendChild(svgEl("text", {
          x: mStartX + 4, y: Math.max(12, barTop - 4),
          "font-size": 11, fill: "#9ca3af",
        }, String(m.measureIndex + 1)));
      }

      let colX = mStartX + m.padLeft;
      const colPositions = [];

      for (let cIdx = 0; cIdx < m.columns.length; cIdx += 1) {
        const col = m.columns[cIdx];
        const colW = col.minWidth + bonusPerCol;
        const centerX = colX + col.leftPad + col.bodyWidth / 2;
        colPositions.push({ col, colX, centerX, colW });
        colX += colW;
      }

      // 逐声部绘制音符与同拍减时线
      for (const staff of staves) {
        const { baseY } = rowStaffLayout.get(staff);
        const beamCandidates = [];

        for (let cIdx = 0; cIdx < colPositions.length; cIdx += 1) {
          const { col, centerX } = colPositions[cIdx];
          const cell = col.cells.get(staff);
          if (!cell) continue;

          if (cell.kind === "dash") {
            svg.appendChild(svgEl("line", {
              x1: centerX - 7, y1: baseY,
              x2: centerX + 7, y2: baseY,
              stroke: "#1f2430", "stroke-width": 1.8, "stroke-linecap": "round",
            }));
            beamCandidates.push(null);
            continue;
          }

          const metric = measureCellVertical(cell);
          const g = svgEl("g", { class: "jp-note" });
          const primarySources = [];

          // 音符背景高亮胶囊（默认透明，激活时显示浅蓝底）
          const topY = baseY - 11;
          const botY = baseY + metric.stackBody + 11;
          const pill = svgEl("rect", {
            class: "jp-note-bg",
            x: centerX - 10,
            y: topY,
            width: 20,
            height: Math.max(22, botY - topY),
            rx: 5,
            fill: "transparent",
          });
          g.appendChild(pill);

          for (let nIdx = 0; nIdx < cell.notes.length; nIdx += 1) {
            const n = cell.notes[nIdx];
            const cy = baseY + (metric.yOffsets[nIdx] || 0);
            if (n._source) primarySources.push(n._source);

            // 临时升降号（左侧留白区域）
            if (n.acc !== 0) {
              const accText = n.acc > 0 ? "#" : "b";
              g.appendChild(svgEl("text", {
                class: "jp-ink",
                x: centerX - 9,
                y: cy - 2,
                "font-size": 12,
                "font-weight": 700,
                "text-anchor": "end",
                fill: "#1f2430",
              }, accText));
            }

            // 音符数字（1~7 或 0）
            g.appendChild(svgEl("text", {
              class: "jp-ink jp-digit",
              x: centerX,
              y: cy + 7,
              "font-size": 20,
              "font-weight": 600,
              "text-anchor": "middle",
              fill: cell.kind === "rest" ? "#6b7280" : "#1f2430",
            }, n.degree));

            // 高八度点
            if (n.oct > 0) {
              const cnt = Math.min(3, n.oct);
              for (let d = 0; d < cnt; d += 1) {
                g.appendChild(svgEl("circle", {
                  class: "jp-ink",
                  cx: centerX,
                  cy: cy - 11 - d * 4.6,
                  r: 1.7,
                  fill: "#1f2430",
                }));
              }
            }

            // 低八度点：若不是最底层音符，直接紧随数字下方；最底层音符需避让减时线
            if (n.oct < 0) {
              const isLastNote = nIdx === cell.notes.length - 1;
              const startDotY = isLastNote && cell.underlines > 0
                ? cy + 12 + cell.underlines * 4.2 + 4.2
                : cy + 12;
              const cnt = Math.min(3, -n.oct);
              for (let d = 0; d < cnt; d += 1) {
                g.appendChild(svgEl("circle", {
                  class: "jp-ink",
                  cx: centerX,
                  cy: startDotY + d * 4.6,
                  r: 1.7,
                  fill: "#1f2430",
                }));
              }
            }
          }

          // 附点（画在主音最右侧）
          if (cell.dots > 0) {
            const dotCy = baseY + (metric.yOffsets[0] || 0) + 1;
            for (let d = 0; d < cell.dots; d += 1) {
              g.appendChild(svgEl("circle", {
                class: "jp-ink",
                cx: centerX + 10 + d * 5.2,
                cy: dotCy,
                r: 1.75,
                fill: cell.kind === "rest" ? "#6b7280" : "#1f2430",
              }));
            }
          }

          // 减时线基线 Y
          const lastNoteY = baseY + (metric.yOffsets[metric.yOffsets.length - 1] || 0);
          if (cell.underlines > 0) {
            const lineLeft = centerX - 8;
            const lineRight = centerX + 8 + (cell.dots > 0 ? cell.dots * 4 : 0);
            for (let u = 0; u < cell.underlines; u += 1) {
              const ly = lastNoteY + 12 + u * 4.2;
              g.appendChild(svgEl("line", {
                class: "jp-ink",
                x1: lineLeft, y1: ly,
                x2: lineRight, y2: ly,
                stroke: cell.kind === "rest" ? "#6b7280" : "#1f2430",
                "stroke-width": 1.35,
              }));
            }
          }

          // 跨拍/跨小节延音线提示弧
          if (cell.tieStart) {
            const topTieY = baseY - (cell.notes[0] && cell.notes[0].oct > 0 ? 18 + Math.min(3, cell.notes[0].oct) * 4.5 : 13);
            const nextPos = colPositions[cIdx + 1];
            const endX = nextPos ? nextPos.centerX - 4 : centerX + 18;
            const midX = (centerX + endX) / 2;
            g.appendChild(svgEl("path", {
              class: "jp-ink",
              d: `M ${centerX + 3} ${topTieY} Q ${midX} ${topTieY - 8} ${endX} ${topTieY}`,
              fill: "none",
              stroke: "#1f2430",
              "stroke-width": 1.3,
            }));
          } else if (cell.tieEnd && cIdx === 0) {
            const topTieY = baseY - (cell.notes[0] && cell.notes[0].oct > 0 ? 18 + Math.min(3, cell.notes[0].oct) * 4.5 : 13);
            g.appendChild(svgEl("path", {
              class: "jp-ink",
              d: `M ${centerX - 16} ${topTieY} Q ${centerX - 8} ${topTieY - 7} ${centerX - 3} ${topTieY}`,
              fill: "none",
              stroke: "#1f2430",
              "stroke-width": 1.3,
            }));
          }

          svg.appendChild(g);
          if (primarySources.length) {
            noteItems.push({ el: g, sources: primarySources, rowEl: svg });
          }

          beamCandidates.push({
            beatGroup: Math.floor((col.beatOffset + EPS) / beatUnitQuarters),
            underlines: cell.underlines,
            stackHeight: metric.stackBody,
            centerX,
            lastNoteY,
          });
        }

        // 同拍相邻减时音符：连接横向共线，使简谱节奏一目了然
        for (let b = 0; b < beamCandidates.length - 1; b += 1) {
          const cur = beamCandidates[b];
          const nxt = beamCandidates[b + 1];
          if (!cur || !nxt) continue;
          if (cur.beatGroup !== nxt.beatGroup) continue;
          const shared = Math.min(cur.underlines, nxt.underlines);
          if (shared <= 0 || Math.abs(cur.lastNoteY - nxt.lastNoteY) > 1 || (nxt.centerX - cur.centerX) > 76) continue;
          for (let u = 0; u < shared; u += 1) {
            const ly = cur.lastNoteY + 12 + u * 4.2;
            svg.appendChild(svgEl("line", {
              x1: cur.centerX + 6, y1: ly,
              x2: nxt.centerX - 6, y2: ly,
              stroke: "#1f2430", "stroke-width": 1.35,
            }));
          }
        }
      }

      xCursor = mStartX + mWidth;
      // 小节线（末小节加粗双线）
      const isVeryLast = row.isLastRow && mIdx === row.measures.length - 1;
      if (isVeryLast) {
        svg.appendChild(svgEl("line", {
          x1: xCursor - 4, y1: barTop, x2: xCursor - 4, y2: barBottom,
          stroke: "#1f2430", "stroke-width": 1.2,
        }));
        svg.appendChild(svgEl("line", {
          x1: xCursor, y1: barTop, x2: xCursor, y2: barBottom,
          stroke: "#1f2430", "stroke-width": 2.8,
        }));
      } else {
        svg.appendChild(svgEl("line", {
          x1: xCursor, y1: barTop, x2: xCursor, y2: barBottom,
          stroke: "#374151", "stroke-width": 1.3,
        }));
      }
    }

    return { svg, noteItems };
  }

  /**
   * 对外暴露的简谱渲染器控制对象。
   */
  class JianpuRenderer {
    constructor(host, options) {
      this.host = host;
      this.options = options || {};
      this.noteItems = [];
      this.activeEls = new Set();
      this.renderToken = 0;
      this.rafId = null;
      this.lastRowScrolled = null;
    }

    destroy() {
      this.renderToken += 1;
      if (this.rafId) {
        cancelAnimationFrame(this.rafId);
        this.rafId = null;
      }
      this.noteItems = [];
      this.activeEls.clear();
      this.lastRowScrolled = null;
    }

    render(score, partValue) {
      this.destroy();
      const token = this.renderToken;
      this.host.innerHTML = "";
      if (!score || !score.events || !score.events.length) {
        const empty = document.createElement("div");
        empty.className = "jp-empty";
        empty.textContent = "当前曲谱暂无可展示的简谱音符";
        this.host.appendChild(empty);
        return;
      }

      const degreeFn = this.options.midiToDegree;
      const isMinorFn = this.options.isMinor;
      const measureLen = getMeasureQuarterLen(score);
      const beatQuarterLen = 4 / Math.max(1, Number(score.beat_unit) || 4);

      const hasBass = Boolean(score.piano) || score.events.some((e) => (Number(e.staff) || 0) === 1);
      let staves = [0];
      if (partValue === "1" && hasBass) staves = [1];
      else if (partValue === "0") staves = [0];
      else if (hasBass) staves = [0, 1];

      const split = splitEventsAtMeasures(score.events, measureLen);
      const maxMeasure = split.reduce((mx, e) => Math.max(mx, e.measure || 0), 0);

      // 按小节与声部聚拢
      const byMeasure = new Map();
      for (const ev of split) {
        if (!staves.includes(ev.staff)) continue;
        if (!byMeasure.has(ev.measure)) byMeasure.set(ev.measure, new Map());
        const mMap = byMeasure.get(ev.measure);
        if (!mMap.has(ev.staff)) mMap.set(ev.staff, []);
        mMap.get(ev.staff).push(ev);
      }

      const measures = [];
      for (let m = 0; m <= maxMeasure; m += 1) {
        const mMap = byMeasure.get(m) || new Map();
        measures.push(
          buildMeasureLayout(m, m * measureLen, measureLen, mMap, staves, score, degreeFn),
        );
      }

      const hostWidth = Math.max(360, (this.host.clientWidth || 920) - 8);
      const labelReserve = staves.length > 1 ? 44 : 4;
      const rows = packMeasuresIntoRows(measures, hostWidth, labelReserve);
      if (rows.length > 0) rows[rows.length - 1].isLastRow = true;

      // 曲首调号/拍号/速度头
      const headerWrap = document.createElement("div");
      headerWrap.className = "jp-header-row";
      headerWrap.appendChild(renderHeaderSvg(score, hostWidth, isMinorFn));
      this.host.appendChild(headerWrap);

      const listWrap = document.createElement("div");
      listWrap.className = "jp-rows";
      this.host.appendChild(listWrap);

      const appendRow = (idx) => {
        const row = rows[idx];
        const rowDiv = document.createElement("div");
        rowDiv.className = "jp-line-wrap";
        const { svg, noteItems } = renderRowSvg(
          row, idx, staves, hostWidth, labelReserve, beatQuarterLen,
        );
        rowDiv.appendChild(svg);
        listWrap.appendChild(rowDiv);
        for (const item of noteItems) {
          item.rowEl = rowDiv;
          this.noteItems.push(item);
        }
      };

      const IMMEDIATE_ROWS = 28;
      const firstBatch = Math.min(rows.length, IMMEDIATE_ROWS);
      for (let i = 0; i < firstBatch; i += 1) appendRow(i);

      if (firstBatch < rows.length) {
        let nextIdx = firstBatch;
        const step = () => {
          if (token !== this.renderToken) return;
          const end = Math.min(rows.length, nextIdx + 24);
          for (; nextIdx < end; nextIdx += 1) appendRow(nextIdx);
          if (nextIdx < rows.length) {
            this.rafId = requestAnimationFrame(step);
          } else {
            this.rafId = null;
          }
        };
        this.rafId = requestAnimationFrame(step);
      }
    }

    updateHighlight(soundingSet) {
      const nextActive = new Set();
      let firstRow = null;

      for (let i = 0; i < this.noteItems.length; i += 1) {
        const item = this.noteItems[i];
        let active = false;
        for (let j = 0; j < item.sources.length; j += 1) {
          if (soundingSet.has(item.sources[j])) {
            active = true;
            break;
          }
        }
        if (active) {
          nextActive.add(item.el);
          if (!firstRow) firstRow = item.rowEl;
        }
      }

      for (const el of this.activeEls) {
        if (!nextActive.has(el)) el.classList.remove("is-active");
      }
      for (const el of nextActive) {
        if (!this.activeEls.has(el)) el.classList.add("is-active");
      }
      this.activeEls = nextActive;

      // 当活跃行离开可见区域（注意避开底部 160px 琴键演奏条）时平滑滚动
      if (firstRow && firstRow !== this.lastRowScrolled) {
        this.lastRowScrolled = firstRow;
        const rect = firstRow.getBoundingClientRect();
        const safeBottom = window.innerHeight - 170;
        if (rect.top < 74 || rect.bottom > safeBottom) {
          firstRow.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }
    }

    clearHighlight() {
      for (const el of this.activeEls) el.classList.remove("is-active");
      this.activeEls.clear();
      this.lastRowScrolled = null;
    }
  }

  global.JianpuSvgEngine = {
    JianpuRenderer,
    // 导出内部辅助函数便于自动化单元测试
    _internals: {
      getMeasureQuarterLen,
      splitEventsAtMeasures,
      normalizeStaffMeasure,
      decomposeDuration,
      buildMeasureLayout,
      packMeasuresIntoRows,
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
