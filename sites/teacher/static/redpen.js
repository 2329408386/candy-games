/* 语文老师工作台: draws a review the way a Chinese teacher marks an exercise book, in red pen.
 *
 * RedPen.render(container, data) — data comes from redpen_data() in core/essay/__init__.py:
 *   text, title, student, marks [{type, start, end, fix, note}], total, max, level, showScore, summary,
 *   pages [{src, w, h, lines: [{s, e, b: [x1, y1, x2, y2] (0–1000)}]}], onPhoto.
 * RedPen.exportPdf(container) — the same drawing on A4 pages, as a PDF to print.
 * RedPen.exportMany(list, fileName, button, note) — several essays (a list of such data) in one PDF, each
 *   starting on a new page (批量导出 on the assignment page). Android WeChat cannot save a blob: file, so there the
 *   PDF goes to the server and a dialog gives a link for a while, with `note` above it; the answer's `link` says so.
 * RedPen.deliverPdf(pages [{w, h, jpeg}], name, button, note, size) — hands JPEG pages over as a PDF the same way;
 *   size is the page in pt (default A4 portrait, RedPen.A3_LANDSCAPE for 班级作文报).
 *
 * With onPhoto the marks are drawn on the student's own photos: every recognised line knows which characters of
 * the text it holds and where it is, and a character's place within its line is estimated from its position
 * (handwriting is roughly even). Otherwise (typed essays) the text is laid out on squared composition paper
 * (作文纸) and the same marks are drawn there; photos without usable line places are shown above it.
 *
 * Drawing goes layout → scene (paths, handwritten text, images) → renderer: SVG on the page (tap a mark for its
 * full note), canvas for the PDF, so both look the same.
 *
 * Proofreading marks (修改符号, the shapes of the 修改病句 PK game in shared-static, pk types edit.js): the correction
 * of an error (quote → fix) is diffed character by character and drawn as 改正号 (loop the wrong characters, the
 * right ones written above), 增补号 (∧ under the gap, the missing characters above), 删除号 (loop with a pig's
 * tail), 对调号 (the line under one part and over the other); a correction too tangled for marks is underlined and
 * written out in a note. 好词 get circles under each character, 好句 a wavy line, 修改建议 brackets, 分段 the
 * 分段号. Short notes are written on the photo between the lines; a longer note gets a small circled number
 * (①②③, in reading order) at the end of its mark: on the page a tap on the mark shows it; in the PDF each sheet
 * gets an A4 page with the numbered notes written in the margins on both sides (framed()), the 总评 under the last. The score goes in the top right corner, the 总评 under the essay — all in a
 * neat handwritten 楷书 (霞鹜文楷), each character only slightly uneven.
 */
(function () {
  'use strict';
  const RED = '#d2342a';
  const PEN_FONT = "RedPen, 'Kaiti SC', STKaiti, KaiTi, serif";
  const TYPED_FONT = "'Kaiti SC', STKaiti, KaiTi, serif";
  const NS = 'http://www.w3.org/2000/svg';
  const KIND = { 好词好句: 'good', 错别字: 'typo', 用词不当: 'word', 病句: 'line', 标点: 'line', 修改建议: 'tip', 分段: 'para', 书写提醒: 'soft' };
  const INLINE_CHARS = 8; // notes longer than this go in the margin
  const FIX_INLINE = 6; // corrections longer than this are written out in the margin
  const COLS = 20;
  const NO_LINE_START = '，。、；：？！”’）》…—';

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs) node.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(node);
    return node;
  }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
  }

  const weight = (c) => (/[\x20-\x7e]/.test(c) ? 0.55 : 1);
  const textWidth = (s, size) => [...s].reduce((a, c) => a + weight(c), 0) * size;
  const f1 = (n) => n.toFixed(1);

  // A seeded wobble, so a hand-drawn line looks the same every time the page is drawn.
  function rng(seed) {
    let x = Math.abs(Math.floor(seed)) % 233280;
    return () => ((x = (x * 9301 + 49297) % 233280) / 233280) - 0.5;
  }

  /* A pen stroke through points: each segment bows a little, like a hand. */
  function stroke(points, rand, amp) {
    let d = 'M' + f1(points[0][0]) + ' ' + f1(points[0][1]);
    for (let k = 1; k < points.length; k++) {
      const [x0, y0] = points[k - 1], [x1, y1] = points[k];
      const len = Math.hypot(x1 - x0, y1 - y0) || 1;
      const bow = rand() * amp;
      const mx = (x0 + x1) / 2 - ((y1 - y0) / len) * bow, my = (y0 + y1) / 2 + ((x1 - x0) / len) * bow;
      d += ' Q' + f1(mx) + ' ' + f1(my) + ' ' + f1(x1 + rand() * amp * 0.3) + ' ' + f1(y1 + rand() * amp * 0.3);
    }
    return d;
  }

  /* ---- where characters are ---- */
  /* Segments of a range of characters: one box per line it touches. lines: [{s, e, x1, y1, x2, y2, text}]. */
  function segments(lines, mark) {
    const out = [];
    for (const L of lines) {
      const a = Math.max(L.s, mark.start), b = Math.min(L.e, mark.end);
      if (a >= b) continue;
      const cum = [0];
      for (const c of L.text) cum.push(cum[cum.length - 1] + weight(c));
      const total = cum[cum.length - 1] || 1;
      const at = (k) => L.x1 + ((L.x2 - L.x1) * cum[k - L.s]) / total;
      out.push({ x1: at(a), x2: at(b), y1: L.y1, y2: L.y2, h: L.y2 - L.y1, L });
    }
    return out;
  }

  /* The gap before character k (an insertion point): x and the line's box. */
  function gapAt(lines, k) {
    const after = segments(lines, { start: k, end: k + 1 })[0];
    const before = segments(lines, { start: k - 1, end: k })[0];
    // Inside a line the gap is the left edge of the next character; at the end of a line, the right edge of the last.
    if (after) return { ...after, x: after.x1 };
    if (before) return { ...before, x: before.x2 };
    return null;
  }

  /* ---- the correction, character by character ---- */
  /* Hunks turning a into b ({a0, a1, ins}, positions in a), or a swap, or null when too tangled to draw. */
  function editOps(a, b) {
    a = [...a];
    b = [...b];
    const n = a.length, m = b.length;
    if (!m || n > 120 || m > 160) return null;
    const dp = Array.from({ length: n + 1 }, () => new Int16Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    const hunks = [];
    let i = 0, j = 0, cur = null;
    const open = () => cur || (cur = { a0: i, a1: i, ins: '' });
    while (i < n || j < m) {
      if (i < n && j < m && a[i] === b[j]) {
        if (cur) hunks.push(cur), (cur = null);
        i++, j++;
      } else if (j >= m || (i < n && dp[i + 1][j] >= dp[i][j + 1])) {
        open().a1 = ++i;
      } else {
        open().ins += b[j++];
      }
    }
    if (cur) hunks.push(cur);
    if (!hunks.length) return null;
    if (hunks.length === 2) {
      const [h, k] = hunks;
      const del = (x) => !x.ins && x.a1 > x.a0, ins = (x) => x.ins && x.a1 === x.a0;
      if (k.a0 > h.a1 && del(h) && ins(k) && a.slice(h.a0, h.a1).join('') === k.ins) return { swap: [h.a0, h.a1, k.a0] };
      if (k.a0 > h.a1 && ins(h) && del(k) && a.slice(k.a0, k.a1).join('') === h.ins) return { swap: [h.a0, k.a0, k.a1] };
    }
    const changed = hunks.reduce((s, x) => s + (x.a1 - x.a0) + [...x.ins].length, 0);
    if (hunks.length > 3 || changed > 0.6 * (n + m) || hunks.some((x) => [...x.ins].length > FIX_INLINE)) return null;
    return { hunks };
  }

  /* ---- scene ---- */
  const path = (d, sw, extra) => Object.assign({ t: 'path', d, sw }, extra || {});
  const pen = (s, x, y, size, seed, extra) => Object.assign({ t: 'text', s, x, y, size, seed }, extra || {});

  /* Where each character of handwritten text goes: uneven spacing, a drifting baseline, each one a little turned. */
  function glyphs(item) {
    const chars = [...item.s];
    if (!item.seed && item.seed !== 0) {
      let x = item.x - (item.anchor === 'middle' ? textWidth(item.s, item.size) / 2 : 0);
      return chars.map((c) => {
        const g = { c, x, y: item.y, rot: 0 };
        x += weight(c) * item.size;
        return g;
      });
    }
    const r = rng(item.seed * 7 + 13);
    const slope = r() * 0.015;
    const advance = chars.map((c) => weight(c) * item.size * (0.99 + r() * 0.05));
    let x = item.x - (item.anchor === 'middle' ? advance.reduce((a, b) => a + b, 0) / 2 : 0);
    return chars.map((c, k) => {
      const g = { c, x: x + r() * item.size * 0.02, y: item.y + r() * item.size * 0.04 + slope * (x - item.x), rot: r() * 2.5 };
      x += advance[k];
      return g;
    });
  }

  function overlap(a, b) {
    const w = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1), h = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
    return w > 0 && h > 0 ? w * h : 0;
  }

  /* The least crowded box among options (each {x1, y1, w, h}, moved inside the sheet): writing on the student's
   * words (obstacles) costs, on other notes (taken) costs far more. null when the best one still covers more than
   * `allowed` of its area with the student's writing — the note then gets a number instead. */
  function bestBox(options, sheet, taken, obstacles, allowed) {
    let best = null, bestCost = Infinity, bestInk = 0;
    for (const o of options) {
      const dx = Math.max(0, sheet.x1 - o.x1) - Math.max(0, o.x1 + o.w - sheet.x2);
      const dy = Math.max(0, sheet.y1 - o.y1) - Math.max(0, o.y1 + o.h - sheet.y2);
      const box = { x1: o.x1 + dx, y1: o.y1 + dy, x2: o.x1 + o.w + dx, y2: o.y1 + o.h + dy };
      let ink = 0, cost = 0;
      for (const t of obstacles) ink += overlap(box, t);
      for (const t of taken) cost += overlap(box, t) * 50;
      cost += ink;
      if (cost < bestCost) (best = box), (bestCost = cost), (bestInk = ink);
      if (!cost) break;
    }
    if (!best || bestInk > (best.x2 - best.x1) * (best.y2 - best.y1) * allowed || bestCost > (best.x2 - best.x1) * (best.y2 - best.y1) * 2) return null;
    taken.push(best);
    return best;
  }

  /* A place near a mark for a short note: in the blank between the lines, or after the end of the line. */
  function place(text, size, near, sheet, taken, obstacles) {
    const w = textWidth(text, size), h = size * 1.15;
    const gap = near.h * 0.12;
    const xs = [near.x1, near.x2 - w, near.x1 - w * 0.3];
    const ys = [near.y2 + gap, near.y1 - gap - h, near.y2 + gap + h * 0.6];
    const options = [];
    for (const y of ys) for (const x of xs) options.push({ x1: x, y1: y, w, h });
    options.push({ x1: near.x2 + gap, y1: near.y1, w, h });
    if (near.L) options.push({ x1: near.L.x2 + gap * 2, y1: near.y1, w, h }); // the blank after a short line
    return bestBox(options, sheet, taken, obstacles, 0.04);
  }

  /* How much blank there is above and below each line (to the nearest line it shares columns with): the red
   * writing between the lines is sized to fit there, so it does not cover the student's words. */
  function roomAround(lines, sheet) {
    for (const L of lines) {
      const lh = L.y2 - L.y1;
      let up = L.y1 - sheet.y1, down = sheet.y2 - L.y2;
      for (const M of lines) {
        if (M === L || Math.min(L.x2, M.x2) - Math.max(L.x1, M.x1) <= 0) continue;
        if (M.y2 <= L.y1 + lh * 0.3) up = Math.min(up, L.y1 - M.y2);
        else if (M.y1 >= L.y2 - lh * 0.3) down = Math.min(down, M.y1 - L.y2);
      }
      L.up = Math.max(0, up);
      L.down = Math.max(0, down);
    }
  }

  /* ---- the marks ---- */
  function loop(seg, rand, k) {
    const cx = (seg.x1 + seg.x2) / 2, cy = (seg.y1 + seg.y2) / 2;
    const rx = (seg.x2 - seg.x1) / 2 + seg.h * 0.22, ry = seg.h * 0.62;
    // An ellipse drawn in one stroke that overshoots its start a little, like a pen does.
    const pts = [];
    for (let j = 0; j <= 26; j++) {
      const t = -0.6 + (j / 24) * Math.PI * 2;
      const r = 1 + rand() * 0.07 * (k || 1);
      pts.push([cx + rx * r * Math.cos(t), cy + ry * r * Math.sin(t)]);
    }
    return { d: 'M' + pts.map((p) => f1(p[0]) + ' ' + f1(p[1])).join(' L'), top: [cx + rx * 0.35, cy - ry * 0.94], right: [cx + rx * 0.92, cy - ry * 0.4] };
  }

  /* A small loop round a punctuation mark: on squared paper it sits in the lower left of its square (or squeezed
   * into the lower right of the character before it, close by), so a loop round the whole square would ring the
   * character next to it. Same shape of answer as loop(). */
  function dotLoop(seg, rand) {
    const h = seg.h, w = Math.max(seg.x2 - seg.x1, h * 0.4);
    const cx = seg.x1 + w * 0.2, cy = seg.y2 - h * 0.32;
    return loop({ x1: cx - h * 0.16, x2: cx + h * 0.16, y1: cy - h * 0.22, y2: cy + h * 0.22, h: h * 0.44 }, rand);
  }
  const PUNCT = /^[，。、；：？！“”‘’（）《》…—,.;:?!"'()]+$/u;

  function wavy(seg, rand) {
    const y = seg.y2 + seg.h * 0.14, amp = seg.h * 0.09, step = seg.h * 0.22;
    let d = 'M' + f1(seg.x1) + ' ' + f1(y), up = true;
    for (let x = seg.x1; x < seg.x2; x += step) {
      const nx = Math.min(seg.x2, x + step);
      d += ' Q' + f1((x + nx) / 2) + ' ' + f1(y + (up ? -amp : amp) * (1 + rand() * 0.3)) + ' ' + f1(nx) + ' ' + f1(y + rand() * amp * 0.2);
      up = !up;
    }
    return d;
  }

  function underline(seg, rand) {
    const y = seg.y2 + seg.h * 0.12;
    return stroke([[seg.x1, y + rand() * seg.h * 0.05], [(seg.x1 + seg.x2) / 2, y + rand() * seg.h * 0.08], [seg.x2, y + rand() * seg.h * 0.05]], rand, seg.h * 0.05);
  }

  function dots(seg, count, rand) {
    // 圈点: a small circle under each character.
    let d = '';
    const w = (seg.x2 - seg.x1) / count, r = Math.min(w, seg.h) * 0.11;
    for (let k = 0; k < count; k++) {
      const cx = seg.x1 + w * (k + 0.5) + rand() * r, cy = seg.y2 + seg.h * 0.22 + rand() * r * 0.6;
      d += `M${f1(cx + r)} ${f1(cy)} A${f1(r)} ${f1(r * (0.85 + rand() * 0.2))} 0 1 1 ${f1(cx + r - 0.01)} ${f1(cy - r * 0.3)} `;
    }
    return d;
  }

  function bracket(seg, first, last, rand) {
    const pad = seg.h * 0.18, y1 = seg.y1 - pad * 0.6, y2 = seg.y2 + pad * 0.6, hook = seg.h * 0.22;
    let d = '';
    if (first) d += stroke([[seg.x1 - pad + hook, y1], [seg.x1 - pad, y1 + rand()], [seg.x1 - pad, y2]], rand, seg.h * 0.04) + ' ';
    if (last) d += stroke([[seg.x2 + pad - hook, y2], [seg.x2 + pad, y2 + rand()], [seg.x2 + pad, y1]], rand, seg.h * 0.04);
    return d;
  }

  function pigtail(at, h, rand) {
    // 删除号's tail: up out of the loop, a small curl, then off to the right.
    const [x, y] = at, u = h * 0.32;
    return `M${f1(x)} ${f1(y)} C${f1(x + u * 0.6)} ${f1(y - u * 1.4)} ${f1(x + u * 2.1)} ${f1(y - u * 1.2)} ${f1(x + u * 1.5)} ${f1(y - u * 0.5)}` +
      ` C${f1(x + u * 0.9)} ${f1(y + u * 0.1)} ${f1(x + u * 0.6)} ${f1(y - u * 1.6 + rand() * u * 0.3)} ${f1(x + u * 2.6)} ${f1(y - u * 1.9)}`;
  }

  function swapLine(a, b, rand) {
    // 对调号: down at the start, under the first part, up between them, over the second part, down at the end.
    const lo = Math.max(a.y2, b.y2) + a.h * 0.16, hi = Math.min(a.y1, b.y1) - a.h * 0.16;
    return stroke([[a.x1, hi + a.h * 0.2], [a.x1, lo], [a.x2, lo], [b.x1, hi], [b.x2, hi], [b.x2, lo - a.h * 0.2]], rand, a.h * 0.06);
  }

  function paraMark(seg, rand) {
    // 分段号: a bent line in front of where the new paragraph starts, reaching back into the line before.
    const x = seg.x1 - seg.h * 0.12, h = seg.h;
    return stroke([[x - h * 1.6, seg.y1 - h * 0.18], [x, seg.y1 - h * 0.18], [x, seg.y2 + h * 0.18], [x + h * 0.9, seg.y2 + h * 0.18]], rand, h * 0.05);
  }

  /* Draws every mark on one surface (a photo or a sheet of squared paper), in content coordinates; notes too long
   * to write beside the mark get the next number (counter.n) and are returned for the list under the page. */
  function drawMarks(data, lines, sheet, counter) {
    const items = [], numbered = [];
    const taken = [], obstacles = lines.map((L) => ({ x1: L.x1, y1: L.y1, x2: L.x2, y2: L.y2 }));
    roomAround(lines, sheet);
    // Red writing between the lines: as big as the blank allows (never below minFont, never above cap × the line).
    const between = (L, room, h) => Math.min(h * sheet.cap, Math.max(sheet.minFont, room * 0.82));
    // In reading order, so the numbers ①②③ follow the text.
    const order = data.marks.map((m, i) => i).sort((a, b) => data.marks[a].start - data.marks[b].start || a - b);
    order.forEach((i) => {
      const m = data.marks[i];
      const segs = segments(lines, m);
      if (!segs.length) return;
      const kind = KIND[m.type] || 'tip';
      const h = segs[0].h, sw = Math.max(1.4, h * 0.07);
      const rand = rng(m.start * 31 + m.end * 7 + i);
      const g = { t: 'group', mark: i, items: [] };
      items.push(g);
      const draw = (d, w) => g.items.push(path(d, w || sw));
      const write = (s, x, y, size) => g.items.push(pen(s, x, y, size, m.start + x));
      for (const seg of segs) g.items.push({ t: 'hit', x: seg.x1 - h * 0.3, y: seg.y1 - h * 0.4, w: seg.x2 - seg.x1 + h * 0.6, h: seg.h + h * 0.8 });
      const first = segs[0], last = segs[segs.length - 1];
      // A correction written small in the blank above the line, joined by a short stroke from the mark. Returns
      // false (nothing drawn) when there is no blank for it that leaves the student's words uncovered.
      const above = (s, from, cx, line) => {
        const room = line.L ? line.L.up : h;
        const fs = between(line.L, room, h);
        const w = textWidth(s, fs), bh = fs * 1.05;
        // Right of the stroke's end first, so the stroke never crosses the characters.
        const x0 = from ? from[0] + fs * 0.2 : cx - w / 2;
        const y1 = line.y1 - Math.min(h * 0.08, room * 0.1) - bh;
        const options = [x0, cx - w / 2, x0 - w * 0.6, x0 + w * 0.6].map((x1) => ({ x1, y1, w, h: bh }));
        const box = bestBox(options, sheet, taken, obstacles, 0.1);
        if (!box) return false;
        write(s, box.x1, box.y2 - fs * 0.12, fs);
        if (from) draw(stroke([from, [box.x1 - fs * 0.08, box.y2 - fs * 0.3]], rand, h * 0.03), sw * 0.85);
        return true;
      };
      let note = (m.note || '').trim(), long = '';
      if (kind === 'good') {
        const count = m.end - m.start;
        if (count <= 4 && segs.length === 1) draw(dots(first, count, rand), sw * 0.8);
        else for (const seg of segs) draw(wavy(seg, rand));
        note = note || '好！';
      } else if (kind === 'tip') {
        segs.forEach((seg, k) => draw(bracket(seg, k === 0, k === segs.length - 1, rand)));
        if (m.fix) long = (note ? note + '　' : '') + '可以写成：' + m.fix;
      } else if (kind === 'soft') {
        for (const seg of segs) draw(underline(seg, rand)); // not called wrong: no correction marks, the note says it
      } else if (kind === 'para') {
        draw(paraMark(first, rand));
        note = note || '另起一段';
      } else {
        const quote = data.text.slice(m.start, m.end);
        // fix === quote: a character written wrong so that it is no character at all (the photo shows it wrong,
        // the text has the right one): ring it and write it correctly above.
        const same = !!m.fix && m.fix === quote;
        const ops = m.fix && !same ? editOps(quote, m.fix) : null;
        const sub = (a0, a1) => segments(lines, { start: m.start + a0, end: m.start + a1 });
        if (same) {
          const lp = loop(first, rand);
          draw(lp.d);
          for (const seg of segs.slice(1)) draw(loop(seg, rand).d);
          if (!above(m.fix, lp.top, (first.x1 + first.x2) / 2, first)) long = '正确写法：' + m.fix;
        } else if (ops && ops.swap) {
          const [p0, p1, p2] = ops.swap, A = sub(p0, p1), B = sub(p1, p2);
          if (A.length === 1 && B.length === 1 && Math.abs(A[0].y1 - B[0].y1) < A[0].h * 0.5) draw(swapLine(A[0], B[0], rand));
          else for (const seg of segs) draw(underline(seg, rand)), (long = '改为：' + m.fix);
        } else if (ops) {
          for (const hk of ops.hunks) {
            if (hk.a1 > hk.a0) {
              const S = sub(hk.a0, hk.a1);
              if (!S.length) continue;
              const ring = PUNCT.test([...quote].slice(hk.a0, hk.a1).join("")) ? dotLoop : loop;
              const lp = ring(S[0], rand);
              draw(lp.d);
              for (const seg of S.slice(1)) draw(ring(seg, rand).d);
              if (hk.ins) {
                if (!above(hk.ins, lp.top, (S[0].x1 + S[0].x2) / 2, S[0])) long = '改为：' + m.fix;
              } else draw(pigtail(lp.right, S[0].h, rand), sw * 0.9); // 删除号
            } else {
              const gap = gapAt(lines, m.start + hk.a0);
              if (!gap) continue;
              const x = gap.x, gh = gap.h;
              // 增补号: ∧ under the gap, a stroke up through it to the missing characters.
              draw(stroke([[x - gh * 0.2, gap.y2 + gh * 0.32], [x, gap.y2 + gh * 0.02], [x + gh * 0.2, gap.y2 + gh * 0.32]], rand, gh * 0.03));
              draw(stroke([[x, gap.y2 + gh * 0.02], [x + rand() * gh * 0.05, gap.y1 - gh * 0.12]], rand, gh * 0.04), sw * 0.85);
              if (!above(hk.ins, [x, gap.y1 - gh * 0.12], x, gap)) long = '改为：' + m.fix;
            }
          }
        } else {
          for (const seg of segs) draw(underline(seg, rand));
          if (m.fix) long = '改为：' + m.fix;
        }
        if (kind === 'typo' && !long && !same) note = ''; // the right word above says it all; the full note is a tap away
      }
      if (long) note = note && !long.startsWith(note) ? note + (/[。！？!?…]$/.test(note) ? '' : '。') + long : long;
      if (!note) return;
      const L = last.L || {};
      const size = Math.min(h * 0.62, between(L, Math.max(L.up || 0, L.down || 0), h));
      const box = [...note].length <= INLINE_CHARS && !long ? place(note, size, last, sheet, taken, obstacles) : null;
      if (box) write(note, box.x1, box.y2 - size * 0.18, size);
      else {
        const n = ++counter.n;
        const lo = segs.reduce((a, b) => (b.x1 < a.x1 ? b : a)), hi = segs.reduce((a, b) => (b.x2 > a.x2 ? b : a));
        numbered.push({ i, n, text: note, y: (first.y1 + first.y2) / 2, left: lo.x1, right: hi.x2 });
        g.n = n;
        // The number, small and raised, just after the end of the mark.
        const r = Math.max(sheet.minFont * 0.42, h * 0.3);
        const cx = Math.min(sheet.x2 - r, last.x2 + r * 1.6), cy = Math.max(sheet.y1 + r, last.y1 - r * 0.15);
        taken.push({ x1: cx - r, y1: cy - r, x2: cx + r, y2: cy + r });
        g.items.push(path(`M${f1(cx + r)} ${f1(cy)} A${f1(r)} ${f1(r)} 0 1 1 ${f1(cx + r - 0.01)} ${f1(cy - r * 0.05)}`, Math.max(1.2, r * 0.12)));
        const digits = String(n), size = r * (digits.length > 1 ? 1.15 : 1.45);
        g.items.push(pen(digits, cx, cy + size * 0.36, size, null, { anchor: 'middle' }));
        g.items.push({ t: 'hit', x: cx - r * 1.3, y: cy - r * 1.3, w: r * 2.6, h: r * 2.6 });
      }
    });
    return { items, numbered };
  }

  function drawScore(data, sheet, line, obstacles) {
    const size = Math.max(22, (sheet.x2 - sheet.x1) * (sheet.scoreRatio || 0.055));
    const label = data.showScore ? data.total + '分' : '';
    const lw0 = label ? textWidth(label, size * 1.1) + size * 0.25 : 0;
    const width = lw0 + textWidth(data.level, size * 0.95);
    const x = sheet.x2 - width - size * 0.6;
    // In the top right corner, above the first line; higher up when that would cover the student's writing.
    let y = Math.max(sheet.y1 + size * 1.05, line.y1 - size * 0.2);
    const box = (yy) => ({ x1: x, y1: yy - size * 1.1, x2: x + width, y2: yy + size * 0.4 });
    if ((obstacles || []).some((o) => overlap(box(y), o))) y = sheet.y1 + size * 1.05;
    const g = { t: 'group', rot: -7, cx: x, cy: y, items: [] };
    if (label) g.items.push(pen(label, x, y, size * 1.1, 3));
    const lx = x + lw0;
    g.items.push(pen(data.level, lx, y, size * 0.95, 5));
    const lw = textWidth(data.level, size * 0.95);
    for (const dy of [size * 0.2, size * 0.36]) g.items.push(path(`M${f1(lx - 2)} ${f1(y + dy)} Q${f1(lx + lw / 2)} ${f1(y + dy + 3)} ${f1(lx + lw + 4)} ${f1(y + dy - 2)}`, size * 0.06));
    return g;
  }

  /* Breaks a note into lines of at most `per` characters, never starting a line with a closing mark. */
  function wrap(text, per) {
    const out = [];
    let line = [];
    for (const c of text) {
      if (line.length >= per && !NO_LINE_START.includes(c)) out.push(line.join('')), (line = []);
      line.push(c);
    }
    if (line.length) out.push(line.join(''));
    return out;
  }

  const CIRCLED = '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳';

  /* For the PDF: a surface with its numbered notes written in the margins on both sides — each note in the margin
   * nearer its mark, level with it where there is room, stacked downwards without overlapping, no leader lines.
   * When the notes do not fit the height of the surface the writing gets smaller rather than the page longer. */
  function framed(scene) {
    const notes = scene.notes || [];
    if (!notes.length) return scene;
    const cw = scene.w, ch = scene.h, M = Math.round(cw * 0.3), pad = M * 0.07;
    const sides = { left: [], right: [] };
    for (const n of notes) sides[n.left < cw - n.right ? 'left' : 'right'].push(n);
    let size = scene.noteSize, layout;
    for (let attempt = 0; attempt < 8; attempt++, size *= 0.88) {
      const lineH = size * 1.32, label = size * 1.15;
      const per = Math.max(3, Math.floor((M - pad * 2 - label) / size));
      const rowsOf = (n) => wrap(n.text, per);
      const need = (list) => list.reduce((t, n) => t + rowsOf(n).length * lineH + size * 0.45, 0);
      const sideOf = { left: [...sides.left], right: [...sides.right] };
      // Too many on one side: hand the notes nearest the middle to the other side.
      for (const [a, b] of [['left', 'right'], ['right', 'left']])
        while (need(sideOf[a]) > ch && need(sideOf[b]) + lineH * 2 < need(sideOf[a])) {
          const mid = sideOf[a].reduce((best, n) => (!best || Math.abs((n.left + n.right) / 2 - cw / 2) < Math.abs((best.left + best.right) / 2 - cw / 2) ? n : best), null);
          sideOf[a].splice(sideOf[a].indexOf(mid), 1);
          sideOf[b].push(mid);
        }
      layout = [];
      let fits = true;
      for (const side of ['left', 'right']) {
        const placed = [];
        let y = size * 0.3;
        for (const n of sideOf[side].sort((p, q) => p.y - q.y || p.n - q.n)) {
          const rows = rowsOf(n);
          const top = Math.max(y, n.y - lineH * 0.55);
          placed.push({ n, rows, top, height: rows.length * lineH });
          y = top + rows.length * lineH + size * 0.45;
        }
        // Pull the stack up when it runs past the bottom.
        let excess = y - ch;
        for (let k = placed.length - 1; k >= 0 && excess > 0; k--) {
          const room = k ? placed[k].top - (placed[k - 1].top + placed[k - 1].height + size * 0.45) : placed[k].top - size * 0.3;
          const lift = Math.min(excess, Math.max(0, room));
          for (let j = k; j < placed.length; j++) placed[j].top -= lift;
          excess -= lift;
        }
        if (excess > 0) fits = false;
        for (const p of placed) layout.push({ ...p, side, size, lineH, label });
      }
      if (fits) break;
    }
    const over = [];
    let bottom = ch;
    for (const p of layout) {
      const x = p.side === 'left' ? pad : M + cw + pad;
      const g = { t: 'group', items: [] };
      g.items.push(pen(CIRCLED[p.n.n - 1] || '(' + p.n.n + ')', x, p.top + p.size * 0.95, p.size, p.n.n * 3 + 1));
      p.rows.forEach((r, k) => g.items.push(pen(r, x + p.label, p.top + p.size * 0.95 + k * p.lineH, p.size, p.n.n * 17 + k)));
      over.push(g);
      bottom = Math.max(bottom, p.top + p.height);
    }
    return {
      w: cw + M * 2,
      h: bottom,
      items: [{ t: 'fill', x: 0, y: 0, w: cw + M * 2, h: bottom, color: '#fffdf6' }, { t: 'group', tx: M, items: scene.items }, ...over],
    };
  }

  const surface = (w, h, items, numbered, noteSize) => ({ w, h, items, notes: numbered, noteSize });

  /* ---- surfaces ---- */
  function photoScene(page, data, isFirst, counter) {
    const sx = page.w / 1000, sy = page.h / 1000;
    const lines = page.lines.map((l) => ({
      s: l.s, e: l.e, text: data.text.slice(l.s, l.e),
      x1: l.b[0] * sx, y1: l.b[1] * sy, x2: l.b[2] * sx, y2: l.b[3] * sy,
    }));
    // On a photo the red writing stays small (the gaps between handwritten lines are narrow) and keeps off the words.
    const sheet = { x1: page.w * 0.01, y1: page.h * 0.005, x2: page.w * 0.99, y2: page.h * 0.995, minFont: page.w * 0.02, cap: 0.6, scoreRatio: 0.04 };
    const { items, numbered } = drawMarks(data, lines, sheet, counter);
    const content = [{ t: 'img', src: page.src, x: 0, y: 0, w: page.w, h: page.h }, ...items];
    if (isFirst && lines[0]) content.push(drawScore(data, sheet, lines[0], lines));
    return surface(page.w, page.h, content, numbered, page.w * 0.032);
  }

  function plainPhotoScene(page) {
    return { w: page.w, h: page.h, items: [{ t: 'img', src: page.src, x: 0, y: 0, w: page.w, h: page.h }] };
  }

  /* Squared composition paper: COLS cells a row, paragraphs indented two cells, a gap under each row for notes. */
  function paperScene(data, counter) {
    const cell = 40, gap = 40, row = cell + gap, margin = 34;
    const width = COLS * cell + margin * 2;
    const lines = [];
    const chars = [];
    let r = 1, c = 0; // row 0 holds the title
    let start = 0;
    for (const p of data.text.split('\n')) {
      const ps = start;
      start += p.length + 1;
      if (!p.trim()) continue;
      c = 2;
      let lineStart = ps;
      for (let k = 0; k < p.length; k++) {
        if (c >= COLS) {
          lines.push({ s: lineStart, e: ps + k, row: r, c0: lineStart === ps ? 2 : 0 });
          r++, (c = 0), (lineStart = ps + k);
        }
        chars.push({ ch: p[k], row: r, col: c });
        c++;
      }
      lines.push({ s: lineStart, e: ps + p.length, row: r, c0: lineStart === ps ? 2 : 0 });
      r++;
    }
    const rows = Math.max(r, 4);
    const height = margin * 2 + rows * row;
    const top = (rw) => margin + rw * row + gap * 0.5;
    const content = [{ t: 'fill', x: 0, y: 0, w: width, h: height, color: '#fffef8' }];
    let boxes = '', cells = '';
    for (let rw = 0; rw < rows; rw++) {
      boxes += `M${margin} ${top(rw)}h${COLS * cell}v${cell}h${-COLS * cell}Z`;
      for (let k = 1; k < COLS; k++) cells += `M${margin + k * cell} ${top(rw)}v${cell}`;
    }
    content.push(path(boxes, 1.2, { color: '#b9d3b4' }), path(cells, 1, { color: '#cfe1cb' }));
    if (data.title) content.push({ t: 'text', s: data.title, x: width / 2, y: top(0) + cell * 0.78, size: cell * 0.75, anchor: 'middle', typed: true });
    for (const ch of chars) content.push({ t: 'text', s: ch.ch, x: margin + ch.col * cell + cell / 2, y: top(ch.row) + cell * 0.78, size: cell * 0.72, anchor: 'middle', typed: true });
    const placed = lines.map((L) => ({
      s: L.s, e: L.e, text: '国'.repeat(L.e - L.s), // one cell per character, whatever it is
      x1: margin + L.c0 * cell, x2: margin + (L.c0 + (L.e - L.s)) * cell, y1: top(L.row), y2: top(L.row) + cell,
    }));
    const sheet = { x1: 4, y1: 4, x2: width - 4, y2: height - 4, minFont: 22, cap: 0.85 };
    const { items, numbered } = drawMarks(data, placed, sheet, counter);
    content.push(...items, drawScore(data, sheet, { y1: top(0) + cell }));
    return surface(width, height, content, numbered, 26);
  }

  /* The 总评: one paragraph in the teacher's hand under the essay, the score signed at the end. */
  function summaryScene(data) {
    const W = 1000, size = 40, lineH = size * 1.75, left = 70;
    const per = Math.floor((W - left * 2) / size);
    const items = [{ t: 'fill', x: 0, y: 0, w: W, h: 10, color: '#fffef8' }];
    let y = 80;
    items.push(pen('总评：', left - 20, y, size * 1.05, 11));
    y += lineH;
    const text = '　　' + (data.summary || '').trim();
    if ((data.summary || '').trim())
      wrap(text, per).forEach((row, k) => {
        items.push(pen(row, left + (k % 2 ? 4 : -3), y, size, 100 + k));
        y += lineH;
      });
    const sign = (data.showScore ? data.total + '分　' : '') + data.level;
    const sw = textWidth(sign, size * 1.15);
    const sx = W - left - sw;
    items.push(pen(sign, sx, y + size * 0.2, size * 1.15, 77));
    items.push(path(`M${f1(sx - 6)} ${f1(y + size * 0.55)} Q${f1(sx + sw / 2)} ${f1(y + size * 0.75)} ${f1(sx + sw + 10)} ${f1(y + size * 0.45)}`, 3));
    const h = y + size * 1.6;
    items[0].h = h;
    return { w: W, h, items, summary: true };
  }

  function scenes(data) {
    const out = [], counter = { n: 0 };
    if (data.onPhoto) data.pages.forEach((p, k) => out.push(photoScene(p, data, k === 0, counter)));
    else {
      for (const p of data.pages) out.push(plainPhotoScene(p));
      out.push(paperScene(data, counter));
    }
    out.push(summaryScene(data));
    return out;
  }

  /* ---- SVG renderer ---- */
  function toSvg(scene, data) {
    const svg = el('svg', { viewBox: `0 0 ${f1(scene.w)} ${f1(scene.h)}`, width: '100%' });
    const add = (item, parent) => {
      if (item.t === 'group') {
        const tf = (item.tx || item.ty ? `translate(${f1(item.tx || 0)} ${f1(item.ty || 0)}) ` : '') + (item.rot ? `rotate(${item.rot} ${f1(item.cx)} ${f1(item.cy)})` : '');
        const attrs = tf ? { transform: tf } : {};
        if (item.mark !== undefined) {
          const m = data.marks[item.mark];
          Object.assign(attrs, { class: 'mark', 'data-i': item.mark, tabindex: 0, role: 'button', 'aria-label': m.type + '：' + (m.note || m.fix || '') });
          if (item.n) attrs['data-n'] = item.n;
        }
        const g = el('g', attrs, parent);
        for (const it of item.items) add(it, g);
      } else if (item.t === 'path') {
        el('path', Object.assign({ d: item.d, 'stroke-width': f1(item.sw) }, item.color ? { stroke: item.color, fill: 'none' } : { class: 'ink' }), parent);
      } else if (item.t === 'fill') {
        el('rect', { x: item.x, y: item.y, width: f1(item.w), height: f1(item.h), fill: item.color }, parent);
      } else if (item.t === 'img') {
        el('image', { href: item.src, x: item.x, y: item.y, width: item.w, height: item.h, preserveAspectRatio: 'none' }, parent);
      } else if (item.t === 'hit') {
        el('rect', { x: f1(item.x), y: f1(item.y), width: f1(item.w), height: f1(item.h), class: 'hit' }, parent);
      } else if (item.t === 'text') {
        const gs = glyphs(item);
        const t = el('text', {
          x: gs.map((g) => f1(g.x)).join(' '),
          y: gs.map((g) => f1(g.y)).join(' '),
          'font-size': f1(item.size),
          class: item.typed ? 'typed' : 'pen',
        }, parent);
        if (!item.typed) t.setAttribute('rotate', gs.map((g) => g.rot.toFixed(1)).join(' '));
        t.textContent = item.s;
      }
    };
    for (const it of scene.items) add(it, svg);
    return svg;
  }

  /* ---- canvas renderer (for the PDF) ---- */
  function drawCanvas(ctx, scene, images) {
    const draw = (item) => {
      ctx.save();
      if (item.t === 'group') {
        if (item.tx || item.ty) ctx.translate(item.tx || 0, item.ty || 0);
        if (item.rot) {
          ctx.translate(item.cx, item.cy);
          ctx.rotate((item.rot * Math.PI) / 180);
          ctx.translate(-item.cx, -item.cy);
        }
        item.items.forEach(draw);
      } else if (item.t === 'path') {
        ctx.strokeStyle = item.color || RED;
        ctx.globalAlpha = item.color ? 1 : 0.92;
        ctx.lineWidth = item.sw;
        ctx.lineCap = ctx.lineJoin = 'round';
        ctx.stroke(new Path2D(item.d));
      } else if (item.t === 'fill') {
        ctx.fillStyle = item.color;
        ctx.fillRect(item.x, item.y, item.w, item.h);
      } else if (item.t === 'img') {
        if (images[item.src]) ctx.drawImage(images[item.src], item.x, item.y, item.w, item.h);
      } else if (item.t === 'text') {
        ctx.font = `${item.size}px ${item.typed ? TYPED_FONT : PEN_FONT}`;
        ctx.textBaseline = 'alphabetic';
        for (const g of glyphs(item)) {
          ctx.save();
          ctx.translate(g.x, g.y);
          ctx.rotate((g.rot * Math.PI) / 180);
          if (!item.typed) {
            ctx.strokeStyle = '#fffdf7';
            ctx.lineWidth = item.size * 0.08;
            ctx.lineJoin = 'round';
            ctx.strokeText(g.c, 0, 0);
          }
          ctx.fillStyle = item.typed ? '#2b2b2b' : RED;
          ctx.fillText(g.c, 0, 0);
          ctx.restore();
        }
      }
      ctx.restore();
    };
    scene.items.forEach(draw);
  }

  function sources(scene, out) {
    const walk = (items) => items.forEach((it) => (it.t === 'img' ? out.add(it.src) : it.t === 'group' && walk(it.items)));
    walk(scene.items);
    return out;
  }

  const A4_PORTRAIT = [595.28, 841.89], A3_LANDSCAPE = [1190.55, 841.89];

  /* A PDF of pages (A4 unless `page` says otherwise), each one JPEG image (no library needed). */
  function makePdf(pages, page) {
    const box = page || A4_PORTRAIT;
    const enc = new TextEncoder();
    const parts = [];
    let size = 0;
    const offsets = [];
    const put = (x) => {
      const b = typeof x === 'string' ? enc.encode(x) : x;
      parts.push(b);
      size += b.length;
    };
    const obj = (n, body) => {
      offsets[n] = size;
      put(n + ' 0 obj\n');
      body();
      put('\nendobj\n');
    };
    put('%PDF-1.4\n%\xe2\xe3\xcf\xd3\n');
    const kids = pages.map((_, k) => 3 + k * 3 + ' 0 R').join(' ');
    obj(1, () => put('<< /Type /Catalog /Pages 2 0 R >>'));
    obj(2, () => put(`<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`));
    pages.forEach((p, k) => {
      const n = 3 + k * 3;
      obj(n, () => put(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${box[0]} ${box[1]}] /Resources << /XObject << /Im0 ${n + 2} 0 R >> >> /Contents ${n + 1} 0 R >>`));
      const content = `q ${box[0]} 0 0 ${box[1]} 0 0 cm /Im0 Do Q`;
      obj(n + 1, () => put(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`));
      obj(n + 2, () => {
        put(`<< /Type /XObject /Subtype /Image /Width ${p.w} /Height ${p.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${p.jpeg.length} >>\nstream\n`);
        put(p.jpeg);
        put('\nendstream');
      });
    });
    const xref = size;
    const count = 3 + pages.length * 3;
    let table = `xref\n0 ${count}\n0000000000 65535 f \n`;
    for (let n = 1; n < count; n++) table += String(offsets[n]).padStart(10, '0') + ' 00000 n \n';
    put(table + `trailer\n<< /Size ${count} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    return new Blob(parts, { type: 'application/pdf' });
  }

  const PAGE_W = 1654, PAGE_H = 2339, PAGE_PAD = 110; // A4 at 200 dpi

  async function loadImages(list) {
    const images = {}, srcs = new Set();
    list.forEach((s) => sources(s, srcs));
    await Promise.all(
      [...srcs].map(async (src) => {
        const img = new Image();
        img.src = src;
        try {
          await img.decode();
          images[src] = img;
        } catch (e) {}
      })
    );
    return images;
  }

  const toJpeg = (c) =>
    new Promise((done) => c.toBlob(async (b) => done({ w: PAGE_W, h: PAGE_H, jpeg: new Uint8Array(await b.arrayBuffer()) }), 'image/jpeg', 0.86));

  /* One essay on fresh A4 pages: the title, the student and the score on top, then each scene (scaled to the
   * page width, moved to a new page when it would have to shrink a lot). Returns the pages as JPEGs. */
  async function essayPages(data, list) {
    const images = await loadImages(list);
    const W = PAGE_W, H = PAGE_H, pad = PAGE_PAD;
    const canvases = [];
    let canvas, ctx, y;
    const fresh = () => {
      canvas = document.createElement('canvas');
      canvas.width = W;
      canvas.height = H;
      ctx = canvas.getContext('2d');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, W, H);
      y = pad;
      canvases.push(canvas);
    };
    fresh();
    ctx.fillStyle = '#222';
    ctx.textAlign = 'center';
    ctx.font = `bold 52px ${TYPED_FONT}`;
    ctx.fillText('《' + (data.title || '作文') + '》', W / 2, y + 44);
    ctx.font = `30px ${TYPED_FONT}`;
    ctx.fillStyle = '#666';
    const who = [data.student, (data.showScore ? data.total + ' / ' + data.max + ' 分　' : '') + data.level].filter(Boolean).join('　·　');
    ctx.fillText(who, W / 2, y + 96);
    ctx.textAlign = 'left';
    y += 140;
    const put = (s, k) => {
      ctx.save();
      ctx.translate((W - s.w * k) / 2, y);
      ctx.scale(k, k);
      drawCanvas(ctx, s, images);
      ctx.restore();
      y += s.h * k + 30;
    };
    // One sheet of the essay per A4 page, its notes in the margins; the 总评 goes under the last sheet, on the
    // same page when everything still reads well at 60 % or more (so a one-page essay prints on one page).
    const sheets = list.filter((s) => !s.summary).map(framed), summary = list.find((s) => s.summary);
    sheets.forEach((s, n) => {
      if (n) fresh();
      const wide = Math.min(1.25, (W - pad * 2) / s.w);
      const room = H - pad - y;
      if (n === sheets.length - 1 && summary) {
        const ks = Math.min(1.1, (W - pad * 2) / summary.w) / wide; // the 总评 relative to the sheet
        const fit = Math.min(1, room / ((s.h + summary.h * ks) * wide + 30));
        if (fit >= 0.6) {
          put(s, wide * fit);
          put(summary, wide * ks * fit);
          return;
        }
        put(s, Math.min(wide, room / s.h));
        fresh();
        put(summary, Math.min(1.1, (W - pad * 2) / summary.w));
        return;
      }
      put(s, Math.min(wide, room / s.h));
    });
    if (!sheets.length && summary) put(summary, Math.min(1.1, (W - pad * 2) / summary.w));
    const out = [];
    for (const c of canvases) {
      out.push(await toJpeg(c));
      c.width = c.height = 0; // free the memory at once (a whole class can be many pages)
    }
    return out;
  }

  function download(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name.replace(/[\\/:*?"<>|]/g, '');
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  const LINK_PART_BYTES = 25 * 1024 * 1024; // the server takes 30 MB at most

  function splitPages(pages) {
    const parts = [[]];
    let size = 0;
    for (const page of pages) {
      if (size && size + page.jpeg.length > LINK_PART_BYTES) {
        parts.push([]);
        size = 0;
      }
      parts[parts.length - 1].push(page);
      size += page.jpeg.length;
    }
    return parts;
  }

  /* Hands the pages over as a file: a download, or on Android WeChat a link from the server. */
  async function deliver(pages, name, button, note, size) {
    if (!WB.androidWeChat()) {
      const blob = makePdf(pages, size);
      download(blob, name);
      return { link: false, blob };
    }
    const parts = splitPages(pages), links = [];
    for (let k = 0; k < parts.length; k++) {
      if (button) button.textContent = parts.length > 1 ? `正在上传 ${k + 1}/${parts.length}…` : '正在上传…';
      const label = parts.length > 1 ? name.replace(/\.pdf$/i, '') + `-第${k + 1}份.pdf` : name;
      const answer = await WB.upload(window.WB_PDF_LINKS + '?name=' + encodeURIComponent(label), makePdf(parts[k], size)).catch((e) => {
        throw e instanceof TypeError ? new Error('上传失败，请检查网络后重试') : e; // fetch itself failed
      });
      links.push(new URL(answer.url, location.href).href);
    }
    WB.linkDialog(links, { title: 'PDF 已生成', open: '打开 PDF', note, warn: '请不要转发给别人。' });
    return { link: true };
  }

  async function busy(button, label, work) {
    const old = button && button.textContent;
    if (button) (button.disabled = true), (button.textContent = label);
    try {
      try {
        await document.fonts.load('40px RedPen', '老师批改');
      } catch (e) {}
      return await work();
    } finally {
      if (button) (button.disabled = false), (button.textContent = old);
    }
  }

  async function exportPdf(container, button) {
    const data = container.rpData, list = container.rpScenes;
    if (!data || !list) return;
    try {
      await busy(button, '正在生成…', async () => {
        const name = ['《' + (data.title || '作文') + '》', data.student, '批改'].filter(Boolean).join('-') + '.pdf';
        const sent = await deliver(await essayPages(data, list), name, button);
        container.rpLastPdf = sent.blob; // for tests
      });
    } catch (e) {
      alert(e.message || '导出失败，请稍后再试');
    }
  }

  /* 批量导出: every essay of a list in one PDF, each starting on a new page. */
  async function exportMany(list, fileName, button, note) {
    if (!list || !list.length) return null;
    return busy(button, '正在生成…', async () => {
      const pages = [];
      for (let k = 0; k < list.length; k++) {
        if (button) button.textContent = `正在生成 ${k + 1}/${list.length}…`;
        pages.push(...(await essayPages(list[k], scenes(list[k]))));
      }
      return deliver(pages, fileName, button, note);
    });
  }

  /* ---- legend: each symbol drawn on two make-believe characters ---- */
  function legend(numbered) {
    const box = document.createElement('p');
    box.className = 'rp-legend';
    const h = 20, fake = [{ s: 0, e: 3, text: '国国国', x1: 6, y1: 8, x2: 66, y2: 8 + h }];
    const sym = {
      写得好: (r) => wavy({ x1: 6, x2: 66, y1: 8, y2: 8 + h, h }, r),
      好词: (r) => dots({ x1: 6, x2: 66, y1: 8, y2: 8 + h, h }, 3, r),
      改正: (r) => loop(segments(fake, { start: 1, end: 2 })[0], r).d + ' M44 6 L52 0',
      增补: (r) => stroke([[30, 36], [36, 26], [42, 36]], r, 1) + ' M36 26 L36 4 L44 4',
      删除: (r) => { const l = loop(segments(fake, { start: 1, end: 2 })[0], r); return l.d + ' ' + pigtail(l.right, h, r); },
      对调: (r) => swapLine(segments(fake, { start: 0, end: 1 })[0], segments(fake, { start: 1, end: 3 })[0], r),
      可以更好: (r) => bracket(segments(fake, { start: 0, end: 3 })[0], true, true, r),
      分段: (r) => paraMark(segments(fake, { start: 2, end: 3 })[0], r),
    };
    const tip = document.createElement('span');
    tip.className = 'rp-tip';
    tip.textContent = '① 带编号的地方，点一下看老师的意见';
    for (const name in sym) {
      const span = document.createElement('span');
      const svg = el('svg', { viewBox: '0 -4 74 44', width: 54, height: 32, 'aria-hidden': 'true' }, span);
      el('path', { d: sym[name](rng(name.length * 11)), class: 'ink', 'stroke-width': 2 }, svg);
      span.appendChild(document.createTextNode(name));
      box.appendChild(span);
    }
    if (numbered) box.appendChild(tip);
    return box;
  }

  function popup(container) {
    const pop = document.createElement('div');
    pop.className = 'rp-pop';
    pop.hidden = true;
    container.appendChild(pop);
    if (container.rpBound) return;
    container.rpBound = true; // render() may run again on the same container (the review page's preview)
    const show = (g, e) => {
      const data = container.rpData, pop = container.querySelector('.rp-pop');
      const m = data.marks[Number(g.dataset.i)];
      const quote = data.text.slice(m.start, m.end);
      const n = Number(g.dataset.n || 0);
      pop.innerHTML =
        '<strong>' + (n ? esc(CIRCLED[n - 1] || n) + ' ' : '') + esc(m.type) + '</strong><q>' + esc(quote) + '</q>' +
        (m.fix && m.type !== '好词好句' && m.type !== '书写提醒' ? '<p>' + (m.type === '修改建议' ? '可以写成' : m.fix === quote ? '正确写法' : '改成') + '：<b>' + esc(m.fix) + '</b></p>' : '') +
        (m.note ? '<p>' + esc(m.note) + '</p>' : '');
      pop.hidden = false;
      const r = container.getBoundingClientRect(), gr = g.getBoundingClientRect();
      const x = Math.min(Math.max(8, (e && e.clientX ? e.clientX : gr.left) - r.left - 20), r.width - pop.offsetWidth - 8);
      pop.style.left = Math.max(8, x) + 'px';
      pop.style.top = gr.bottom - r.top + 8 + 'px';
    };
    // Tapping a mark opens its comment; tapping the same mark again (or the comment itself) closes it.
    const toggle = (g, e) => {
      const pop = container.querySelector('.rp-pop');
      if (!pop.hidden && pop.dataset.open === g.dataset.i) {
        pop.hidden = true;
        delete pop.dataset.open;
      } else {
        show(g, e);
        pop.dataset.open = g.dataset.i;
      }
    };
    container.addEventListener('click', (e) => {
      const g = e.target.closest('.mark');
      const pop = container.querySelector('.rp-pop');
      if (g) toggle(g, e);
      else {
        pop.hidden = true;
        delete pop.dataset.open;
      }
    });
    container.addEventListener('keydown', (e) => {
      const g = e.target.closest && e.target.closest('.mark');
      if (g && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault();
        toggle(g);
      }
    });
  }

  function render(container, data) {
    container.innerHTML = '';
    container.rpData = data;
    container.classList.add('redpen');
    const list = scenes(data);
    container.rpScenes = list;
    for (const s of list) {
      const wrap = document.createElement('div');
      wrap.className = 'rp-page' + (s.summary ? ' rp-summary' : '');
      wrap.appendChild(toSvg(s, data));
      container.appendChild(wrap);
    }
    container.appendChild(legend(list.some((s) => s.notes && s.notes.length))); // numbered notes show on a tap
    popup(container);
  }

  window.RedPen = { render, exportPdf, exportMany, deliverPdf: deliver, A3_LANDSCAPE, scenes, segments, editOps };
})();
