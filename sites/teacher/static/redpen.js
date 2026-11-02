/* 语文老师工作台: draws a review the way a Chinese teacher marks an exercise book, in red pen.
 *
 * RedPen.render(container, data) — data comes from redpen_data() in core/essay/__init__.py:
 *   text, title, marks [{type, start, end, fix, note}], total, max, level, summary, praise, advice,
 *   pages [{src, w, h, lines: [{s, e, b: [x1, y1, x2, y2] (0–1000)}]}], onPhoto.
 * With onPhoto the marks are drawn on the student's own photos: every recognised line knows which characters of
 * the text it holds and where it is, and a character's place within its line is estimated from its position
 * (handwriting is roughly even). Otherwise (typed essays) the text is laid out on squared composition paper
 * (作文纸) and the same marks are drawn there; photos without usable line places are shown above it.
 *
 * Marks: 好词好句 — wavy line under it; 错别字 — circled, the right word written above; 用词不当 / 病句 /
 * 标点 — underlined; 修改建议 — bracketed; each with a short note in the gap between lines. Tapping a mark shows
 * the full note. The score and level go in the top right corner; the comments are written under the essay.
 */
(function () {
  'use strict';
  const RED = '#d2342a';
  const FONT = "RedPen, 'Kaiti SC', STKaiti, KaiTi, 'BiauKai', serif";
  const NS = 'http://www.w3.org/2000/svg';
  const KIND = { 好词好句: 'good', 错别字: 'typo', 用词不当: 'word', 病句: 'line', 标点: 'line', 修改建议: 'tip' };
  const NOTE_CHARS = 12;
  const COLS = 20;

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

  // A seeded wobble, so a hand-drawn line looks the same every time the page is drawn.
  function rng(seed) {
    let x = seed * 9301 + 49297;
    return () => ((x = (x * 9301 + 49297) % 233280) / 233280) - 0.5;
  }

  /* Segments of a mark: one box per line it touches. lines: [{s, e, x1, y1, x2, y2, text}] in drawing units. */
  function segments(lines, mark) {
    const out = [];
    for (const L of lines) {
      const a = Math.max(L.s, mark.start), b = Math.min(L.e, mark.end);
      if (a >= b) continue;
      const cum = [0];
      for (const c of L.text) cum.push(cum[cum.length - 1] + weight(c));
      const total = cum[cum.length - 1] || 1;
      const at = (k) => L.x1 + ((L.x2 - L.x1) * cum[k - L.s]) / total;
      out.push({ x1: at(a), x2: at(b), y1: L.y1, y2: L.y2, h: L.y2 - L.y1 });
    }
    return out;
  }

  function wavy(seg, g, sw, rand) {
    const y = seg.y2 + seg.h * 0.14, amp = seg.h * 0.09, step = seg.h * 0.22;
    let d = 'M' + seg.x1.toFixed(1) + ' ' + y.toFixed(1), up = true;
    for (let x = seg.x1; x < seg.x2; x += step) {
      const nx = Math.min(seg.x2, x + step);
      d += ' Q' + ((x + nx) / 2).toFixed(1) + ' ' + (y + (up ? -amp : amp) * (1 + rand() * 0.3)).toFixed(1) + ' ' + nx.toFixed(1) + ' ' + y.toFixed(1);
      up = !up;
    }
    el('path', { d, class: 'ink', 'stroke-width': sw }, g);
  }

  function underline(seg, g, sw, rand) {
    const y = seg.y2 + seg.h * 0.12;
    const mid = (seg.x1 + seg.x2) / 2;
    el('path', { d: `M${seg.x1} ${y + rand() * seg.h * 0.05} Q${mid} ${y + rand() * seg.h * 0.12} ${seg.x2} ${y + rand() * seg.h * 0.05}`, class: 'ink', 'stroke-width': sw }, g);
  }

  function circle(seg, g, sw, rand) {
    const cx = (seg.x1 + seg.x2) / 2, cy = (seg.y1 + seg.y2) / 2;
    const rx = (seg.x2 - seg.x1) / 2 + seg.h * 0.28, ry = seg.h * 0.66;
    // An ellipse drawn in one stroke that overshoots its start a little, like a pen does.
    const pts = [];
    for (let k = 0; k <= 26; k++) {
      const t = -0.6 + (k / 24) * Math.PI * 2;
      const r = 1 + rand() * 0.06;
      pts.push([cx + rx * r * Math.cos(t), cy + ry * r * Math.sin(t)]);
    }
    el('path', { d: 'M' + pts.map((p) => p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' L'), class: 'ink', 'stroke-width': sw }, g);
  }

  function bracket(seg, g, sw, first, last) {
    const pad = seg.h * 0.18, y1 = seg.y1 - pad * 0.6, y2 = seg.y2 + pad * 0.6, hook = seg.h * 0.22;
    if (first) el('path', { d: `M${seg.x1 - pad + hook} ${y1} L${seg.x1 - pad} ${y1} L${seg.x1 - pad} ${y2}`, class: 'ink', 'stroke-width': sw }, g);
    if (last) el('path', { d: `M${seg.x2 + pad - hook} ${y2} L${seg.x2 + pad} ${y2} L${seg.x2 + pad} ${y1}`, class: 'ink', 'stroke-width': sw }, g);
  }

  function shortNote(m) {
    let s = (m.note || '').trim();
    if (!s && m.fix && KIND[m.type] !== 'typo') s = KIND[m.type] === 'tip' ? '可以写得更好' : '改：' + m.fix;
    if (!s) return '';
    return [...s].length > NOTE_CHARS ? [...s].slice(0, NOTE_CHARS - 1).join('') + '…' : s;
  }

  function overlap(a, b) {
    const w = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1), h = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
    return w > 0 && h > 0 ? w * h : 0;
  }

  /* Writes text in red where it overlaps the fewest other notes and words. */
  function place(text, size, near, sheet, taken, obstacles) {
    const w = textWidth(text, size), h = size * 1.15;
    const gap = near.h * 0.2;
    const xs = [near.x1, near.x2 - w, near.x1 - w * 0.3];
    const ys = [near.y2 + gap + near.h * 0.18, near.y1 - gap - h, near.y2 + gap + h * 1.1];
    const options = [];
    for (const y of ys) for (const x of xs) options.push({ x1: x, y1: y, x2: x + w, y2: y + h });
    options.push({ x1: near.x2 + gap, y1: near.y1, x2: near.x2 + gap + w, y2: near.y1 + h });
    let best = null, bestCost = Infinity;
    for (const o of options) {
      const dx = Math.max(0, sheet.x1 - o.x1) - Math.max(0, o.x2 - sheet.x2);
      const dy = Math.max(0, sheet.y1 - o.y1) - Math.max(0, o.y2 - sheet.y2);
      const box = { x1: o.x1 + dx, y1: o.y1 + dy, x2: o.x2 + dx, y2: o.y2 + dy };
      let cost = 0;
      for (const t of taken) cost += overlap(box, t) * 50;
      for (const t of obstacles) cost += overlap(box, t);
      if (cost < bestCost) (best = box), (bestCost = cost);
      if (!cost) break;
    }
    taken.push(best);
    return best;
  }

  function write(g, text, x, y, size, extra) {
    const t = el('text', Object.assign({ x, y, 'font-size': size.toFixed(1), class: 'pen' }, extra || {}), g);
    t.textContent = text;
    return t;
  }

  /* Draws every mark on one surface (a photo or a sheet of squared paper). */
  function drawMarks(svg, data, lines, sheet, firstLine) {
    const taken = [], obstacles = lines.map((L) => ({ x1: L.x1, y1: L.y1, x2: L.x2, y2: L.y2 }));
    data.marks.forEach((m, i) => {
      const segs = segments(lines, m);
      if (!segs.length) return;
      const kind = KIND[m.type] || 'tip';
      const h = segs[0].h, sw = Math.max(1.6, h * 0.075);
      const rand = rng(m.start * 31 + m.end);
      const g = el('g', { class: 'mark mark-' + kind, 'data-i': i, tabindex: 0, role: 'button', 'aria-label': m.type + '：' + (m.note || m.fix || '') }, svg);
      segs.forEach((seg, k) => {
        el('rect', { x: seg.x1 - h * 0.3, y: seg.y1 - h * 0.4, width: seg.x2 - seg.x1 + h * 0.6, height: seg.h + h * 0.8, class: 'hit' }, g);
        if (kind === 'good') wavy(seg, g, sw, rand);
        else if (kind === 'typo') circle(seg, g, sw, rand);
        else if (kind === 'tip') bracket(seg, g, sw, k === 0, k === segs.length - 1);
        else underline(seg, g, sw, rand);
      });
      const first = segs[0], last = segs[segs.length - 1];
      const size = Math.max(sheet.minFont, h * 0.62);
      if ((kind === 'typo' || (kind === 'word' && m.fix && [...m.fix].length <= 6)) && m.fix) {
        // The right word, written small above the wrong one.
        const fs = Math.max(sheet.minFont, h * 0.6);
        const w = textWidth(m.fix, fs), cx = (first.x1 + first.x2) / 2;
        const box = { x1: cx - w / 2, y1: first.y1 - h * 0.12 - fs * 1.1, x2: cx + w / 2, y2: first.y1 - h * 0.12 };
        taken.push(box);
        write(g, m.fix, box.x1, box.y2 - fs * 0.15, fs);
      }
      const note = kind === 'good' ? (m.note ? shortNote(m) : '好！') : kind === 'typo' ? '' : shortNote(m);
      if (note) {
        const box = place(note, size, last, sheet, taken, obstacles);
        write(g, note, box.x1, box.y2 - size * 0.18, size);
      }
    });
    if (firstLine) drawScore(svg, data, sheet, firstLine);
  }

  function drawScore(svg, data, sheet, line) {
    const size = Math.max(22, (sheet.x2 - sheet.x1) * 0.055);
    const g = el('g', { class: 'score' }, svg);
    const label = data.showScore ? data.total + '分' : '';
    const width = (label ? textWidth(label, size * 1.1) + size * 0.25 : 0) + textWidth(data.level, size * 0.95);
    const x = sheet.x2 - width - size * 0.6;
    const y = Math.max(sheet.y1 + size * 1.05, line.y1 - size * 0.2);
    g.setAttribute('transform', `rotate(-7 ${x} ${y})`);
    if (label) write(g, label, x, y, size * 1.1);
    const lx = x + (label ? textWidth(label, size * 1.1) + size * 0.25 : 0);
    write(g, data.level, lx, y, size * 0.95);
    const lw = textWidth(data.level, size * 0.95);
    for (const dy of [size * 0.2, size * 0.36]) el('path', { d: `M${lx - 2} ${y + dy} Q${lx + lw / 2} ${y + dy + 3} ${lx + lw + 4} ${y + dy - 2}`, class: 'ink', 'stroke-width': size * 0.06 }, g);
  }

  /* ---- surfaces ---- */
  function photoPage(page, data, isFirst) {
    const wrap = document.createElement('div');
    wrap.className = 'rp-page';
    wrap.style.aspectRatio = page.w + ' / ' + page.h;
    const img = document.createElement('img');
    img.src = page.src;
    img.alt = '作文照片';
    wrap.appendChild(img);
    const svg = el('svg', { viewBox: `0 0 ${page.w} ${page.h}`, preserveAspectRatio: 'none' });
    svg.style.setProperty('--sw', 1);
    wrap.appendChild(svg);
    const sx = page.w / 1000, sy = page.h / 1000;
    const lines = page.lines.map((l) => ({
      s: l.s, e: l.e, text: data.text.slice(l.s, l.e),
      x1: l.b[0] * sx, y1: l.b[1] * sy, x2: l.b[2] * sx, y2: l.b[3] * sy,
    }));
    const sheet = { x1: page.w * 0.01, y1: page.h * 0.005, x2: page.w * 0.99, y2: page.h * 0.995, minFont: page.w * 0.034 };
    drawMarks(svg, data, lines, sheet, isFirst && lines[0]);
    return wrap;
  }

  /* Squared composition paper: COLS cells a row, paragraphs indented two cells, a gap under each row for notes. */
  function paperPage(data) {
    const cell = 40, gap = 40, row = cell + gap, margin = 34;
    const width = COLS * cell + margin * 2;
    const lines = [];
    const chars = [];
    let r = 1, c = 0; // row 0 holds the title
    const paragraphs = [];
    let start = 0;
    data.text.split('\n').forEach((p) => {
      paragraphs.push({ s: start, text: p });
      start += p.length + 1;
    });
    for (const p of paragraphs) {
      if (!p.text.trim()) continue;
      c = 2;
      let lineStart = p.s;
      for (let k = 0; k < p.text.length; k++) {
        if (c >= COLS) {
          lines.push({ s: lineStart, e: p.s + k, row: r, c0: lineStart === p.s ? 2 : 0 });
          r++, (c = 0), (lineStart = p.s + k);
        }
        chars.push({ ch: p.text[k], row: r, col: c });
        c++;
      }
      lines.push({ s: lineStart, e: p.s + p.text.length, row: r, c0: lineStart === p.s ? 2 : 0 });
      r++;
    }
    const rows = Math.max(r, 4);
    const height = margin * 2 + rows * row;
    const wrap = document.createElement('div');
    wrap.className = 'rp-page paper';
    wrap.style.aspectRatio = width + ' / ' + height;
    const svg = el('svg', { viewBox: `0 0 ${width} ${height}` });
    wrap.appendChild(svg);
    const top = (rw) => margin + rw * row + gap * 0.5;
    const grid = el('g', { class: 'grid' }, svg);
    for (let rw = 0; rw < rows; rw++) el('rect', { x: margin, y: top(rw), width: COLS * cell, height: cell }, grid);
    let d = '';
    for (let rw = 0; rw < rows; rw++) for (let k = 1; k < COLS; k++) d += `M${margin + k * cell} ${top(rw)}v${cell}`;
    el('path', { d }, grid);
    const ink = el('g', { class: 'typed' }, svg);
    if (data.title) {
      const t = el('text', { x: width / 2, y: top(0) + cell * 0.78, 'font-size': cell * 0.75, 'text-anchor': 'middle' }, ink);
      t.textContent = data.title;
    }
    for (const ch of chars) {
      const t = el('text', { x: margin + ch.col * cell + cell / 2, y: top(ch.row) + cell * 0.78, 'font-size': cell * 0.72, 'text-anchor': 'middle' }, ink);
      t.textContent = ch.ch;
    }
    const placed = lines.map((L) => ({
      s: L.s, e: L.e, text: '国'.repeat(L.e - L.s), // one cell per character, whatever it is
      x1: margin + L.c0 * cell, x2: margin + (L.c0 + (L.e - L.s)) * cell, y1: top(L.row), y2: top(L.row) + cell,
    }));
    const sheet = { x1: 4, y1: 4, x2: width - 4, y2: height - 4, minFont: 22 };
    drawMarks(svg, data, placed, sheet, { y1: top(0) + cell });
    return wrap;
  }

  function comments(data) {
    const box = document.createElement('div');
    box.className = 'rp-comment';
    let html = '';
    if (data.summary) html += '<p class="rp-summary">' + esc(data.summary) + '</p>';
    if (data.praise.length) html += '<p><span class="rp-tag">好在</span>' + data.praise.map(esc).join('；') + '</p>';
    if (data.advice.length) html += '<p><span class="rp-tag">改一改</span>' + data.advice.map(esc).join('；') + '</p>';
    html += '<p class="rp-sign">' + (data.showScore ? esc(data.total) + ' / ' + esc(data.max) + ' 分　' : '') + esc(data.level) + '</p>';
    box.innerHTML = html;
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
      pop.innerHTML =
        '<strong>' + esc(m.type) + '</strong><q>' + esc(quote) + '</q>' +
        (m.fix && m.type !== '好词好句' ? '<p>改成：<b>' + esc(m.fix) + '</b></p>' : '') +
        (m.note ? '<p>' + esc(m.note) + '</p>' : '');
      pop.hidden = false;
      const r = container.getBoundingClientRect(), gr = g.getBoundingClientRect();
      const x = Math.min(Math.max(8, (e && e.clientX ? e.clientX : gr.left) - r.left - 20), r.width - pop.offsetWidth - 8);
      pop.style.left = Math.max(8, x) + 'px';
      pop.style.top = gr.bottom - r.top + 8 + 'px';
    };
    container.addEventListener('click', (e) => {
      const g = e.target.closest('.mark');
      if (g) show(g, e);
      else if (!e.target.closest('.rp-pop')) container.querySelector('.rp-pop').hidden = true;
    });
    container.addEventListener('keydown', (e) => {
      const g = e.target.closest && e.target.closest('.mark');
      if (g && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault();
        show(g);
      }
    });
  }

  function render(container, data) {
    container.innerHTML = '';
    container.rpData = data;
    container.classList.add('redpen');
    if (data.onPhoto) {
      data.pages.forEach((p, k) => container.appendChild(photoPage(p, data, k === 0)));
    } else {
      for (const p of data.pages) {
        const img = document.createElement('img');
        img.className = 'photo-plain';
        img.src = p.src;
        img.alt = '作文照片';
        container.appendChild(img);
      }
      container.appendChild(paperPage(data));
    }
    container.appendChild(comments(data));
    const legend = document.createElement('p');
    legend.className = 'rp-legend';
    legend.innerHTML = '<span>〰 写得好</span><span>◯ 错别字</span><span>＿ 要改</span><span>［ ］ 可以更好</span>';
    container.appendChild(legend);
    popup(container);
  }

  window.RedPen = { render, segments };
})();
