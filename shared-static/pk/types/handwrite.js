// 字母奇妙屋 (English): write letters by hand on English four-line paper. One box per letter: as wide as a 田字格
// box, as tall as the four lines of an English exercise book (the third line, the baseline, is red).
// q = {kind ("upper-to-lower" | "lower-to-upper"), prompt, answer (the letter to write), shown (the letter given)}.
// Nothing is read aloud. A stylus reads best and a mouse worst; the start page says so (cfg.highlight in letters.html).
//
// Recognition runs in the browser. Every letter has template strokes in four-line coordinates: x in line gaps,
// y = 0 on the top line, 1 on the second, 2 on the red baseline and 3 on the bottom line. A drawing is compared with
// every template twice:
//   shape     $P point-cloud distance (Vatavu, Anthony & Wobbrock 2012): stroke order, direction and count do not
//             matter, size and place do not either;
//   position  where the ink starts and ends between the four lines: capitals fill the top two spaces, a c e … sit in
//             the middle space, b d h k l t reach the top line and g j p q y the bottom line. This is what tells
//             C/c, O/o, S/s, V/v, W/w, X/x, Z/z and P/p apart.
// The best total wins. The expected letter also counts when it is within a small margin of the winner, so a
// slightly wobbly letter is not marked wrong. I and l are the same stroke on four-line paper, so either is accepted.
(() => {
  'use strict';
  const { el, button } = PK;

  // ---------- templates ----------
  const arc = (cx, cy, rx, ry, from, to, steps = 16) =>
    Array.from({ length: steps + 1 }, (_, i) => {
      const t = ((from + ((to - from) * i) / steps) * Math.PI) / 180;
      return [cx + rx * Math.cos(t), cy + ry * Math.sin(t)];
    });
  const ring = (cx, cy, rx, ry) => arc(cx, cy, rx, ry, -30, -390, 24);
  const dot = (x, y) => [[x, y], [x + 0.02, y + 0.02]];
  const cat = (...parts) => [].concat(...parts);
  const T = {
    A: [[[0, 2], [0.6, 0], [1.2, 2]], [[0.27, 1.2], [0.93, 1.2]]],
    B: [[[0, 0], [0, 2]], cat([[0, 0], [0.5, 0]], arc(0.5, 0.5, 0.45, 0.5, -90, 90), [[0, 1]]), cat([[0, 1], [0.55, 1]], arc(0.55, 1.5, 0.5, 0.5, -90, 90), [[0, 2]])],
    C: [arc(0.85, 1, 0.85, 1, -40, -320, 24)],
    D: [[[0, 0], [0, 2]], cat([[0, 0], [0.35, 0]], arc(0.35, 1, 0.8, 1, -90, 90, 20), [[0, 2]])],
    E: [[[1, 0], [0, 0], [0, 2], [1, 2]], [[0, 1], [0.85, 1]]],
    F: [[[1, 0], [0, 0], [0, 2]], [[0, 1], [0.85, 1]]],
    G: [cat(arc(0.85, 1, 0.85, 1, -40, -360, 24), [[1.7, 1.9]]), [[1.15, 1.15], [1.7, 1.15]]],
    H: [[[0, 0], [0, 2]], [[1.1, 0], [1.1, 2]], [[0, 1], [1.1, 1]]],
    I: [[[0, 0], [0, 2]]],
    J: [cat([[0.8, 0], [0.8, 1.5]], arc(0.4, 1.5, 0.4, 0.5, 0, 180))],
    K: [[[0, 0], [0, 2]], [[1, 0], [0, 1.15]], [[0.35, 0.8], [1.05, 2]]],
    L: [[[0, 0], [0, 2], [0.9, 2]]],
    M: [[[0, 2], [0, 0], [0.65, 1.3], [1.3, 0], [1.3, 2]]],
    N: [[[0, 2], [0, 0], [1.1, 2], [1.1, 0]]],
    O: [ring(0.8, 1, 0.8, 1)],
    P: [[[0, 0], [0, 2]], cat([[0, 0], [0.5, 0]], arc(0.5, 0.5, 0.5, 0.5, -90, 90), [[0, 1]])],
    Q: [ring(0.8, 1, 0.8, 1), [[0.95, 1.4], [1.7, 2.1]]],
    R: [[[0, 0], [0, 2]], cat([[0, 0], [0.5, 0]], arc(0.5, 0.5, 0.5, 0.5, -90, 90), [[0, 1]]), [[0.4, 1], [1.1, 2]]],
    S: [cat(arc(0.6, 0.5, 0.6, 0.5, -15, -270), arc(0.6, 1.5, 0.6, 0.5, -90, 165))],
    T: [[[0, 0], [1.2, 0]], [[0.6, 0], [0.6, 2]]],
    U: [cat([[0, 0], [0, 1.4]], arc(0.6, 1.4, 0.6, 0.6, 180, 0), [[1.2, 0]])],
    V: [[[0, 0], [0.6, 2], [1.2, 0]]],
    W: [[[0, 0], [0.4, 2], [0.8, 0.4], [1.2, 2], [1.6, 0]]],
    X: [[[0, 0], [1.2, 2]], [[1.2, 0], [0, 2]]],
    Y: [[[0, 0], [0.6, 1]], [[1.2, 0], [0.6, 1], [0.6, 2]]],
    Z: [[[0, 0], [1.2, 0], [0, 2], [1.2, 2]]],
    a: [ring(0.45, 1.5, 0.45, 0.5), [[0.9, 1], [0.9, 2]]],
    b: [[[0, 0], [0, 2]], ring(0.45, 1.5, 0.45, 0.5)],
    c: [arc(0.45, 1.5, 0.45, 0.5, -40, -320, 20)],
    d: [ring(0.45, 1.5, 0.45, 0.5), [[0.9, 0], [0.9, 2]]],
    e: [cat([[0, 1.5], [0.9, 1.5]], arc(0.45, 1.5, 0.45, 0.5, 0, -315, 20))],
    f: [cat(arc(0.75, 0.4, 0.35, 0.4, -30, -180), [[0.4, 2]]), [[0.05, 1], [0.8, 1]]],
    g: [ring(0.45, 1.5, 0.45, 0.5), cat([[0.9, 1], [0.9, 2.6]], arc(0.45, 2.6, 0.45, 0.4, 0, 160))],
    h: [[[0, 0], [0, 2]], cat(arc(0.45, 1.45, 0.45, 0.45, -180, 0), [[0.9, 2]])],
    i: [[[0, 1], [0, 2]], dot(0, 0.55)],
    j: [cat([[0.45, 1], [0.45, 2.6]], arc(0.05, 2.6, 0.4, 0.4, 0, 150)), dot(0.45, 0.55)],
    k: [[[0, 0], [0, 2]], [[0.8, 1], [0, 1.6]], [[0.3, 1.4], [0.85, 2]]],
    l: [[[0, 0], [0, 2]]],
    m: [[[0, 1], [0, 2]], cat(arc(0.35, 1.4, 0.35, 0.4, -180, 0), [[0.7, 2]]), cat(arc(1.05, 1.4, 0.35, 0.4, -180, 0), [[1.4, 2]])],
    n: [[[0, 1], [0, 2]], cat(arc(0.45, 1.45, 0.45, 0.45, -180, 0), [[0.9, 2]])],
    o: [ring(0.45, 1.5, 0.45, 0.5)],
    p: [[[0, 1], [0, 3]], ring(0.45, 1.5, 0.45, 0.5)],
    q: [ring(0.45, 1.5, 0.45, 0.5), [[0.9, 1], [0.9, 3]]],
    r: [[[0, 1], [0, 2]], arc(0.45, 1.45, 0.45, 0.45, -180, -50)],
    s: [cat(arc(0.4, 1.25, 0.4, 0.25, -15, -270), arc(0.4, 1.75, 0.4, 0.25, -90, 165))],
    t: [[[0.3, 0.35], [0.3, 1.8], [0.45, 2], [0.7, 1.9]], [[0, 1], [0.7, 1]]],
    u: [cat([[0, 1], [0, 1.55]], arc(0.4, 1.55, 0.4, 0.45, 180, 0), [[0.8, 1]]), [[0.8, 1], [0.8, 2]]],
    v: [[[0, 1], [0.45, 2], [0.9, 1]]],
    w: [[[0, 1], [0.3, 2], [0.6, 1.25], [0.9, 2], [1.2, 1]]],
    x: [[[0, 1], [0.9, 2]], [[0.9, 1], [0, 2]]],
    y: [[[0, 1], [0.45, 2]], [[0.9, 1], [0.2, 3]]],
    z: [[[0, 1], [0.9, 1], [0, 2], [0.9, 2]]],
  };
  // Other common ways to write a letter (print a and g, curly y and j, crossed J, serif I).
  const VARIANTS = [
    ['a', [cat(arc(0.45, 1.3, 0.4, 0.3, -160, 0), [[0.85, 2]]), cat([[0.85, 1.5]], arc(0.42, 1.68, 0.43, 0.32, -30, -330))]],
    ['g', [ring(0.45, 1.5, 0.45, 0.5), cat([[0.9, 1], [0.9, 2.5]], arc(0.5, 2.5, 0.4, 0.5, 0, 180))]],
    ['y', [cat([[0, 1], [0, 1.55]], arc(0.4, 1.55, 0.4, 0.45, 180, 0)), cat([[0.8, 1], [0.8, 2.6]], arc(0.4, 2.6, 0.4, 0.4, 0, 160))]],
    ['I', [[[-0.4, 0], [0.4, 0]], [[0, 0], [0, 2]], [[-0.4, 2], [0.4, 2]]]],
    ['J', [[[0.4, 0], [1.2, 0]], cat([[0.8, 0], [0.8, 1.5]], arc(0.4, 1.5, 0.4, 0.5, 0, 180))]],
    ['t', [[[0.3, 0.35], [0.3, 2]], [[0, 1], [0.7, 1]]]],
    ['f', [[[0.4, 0], [0.4, 2]], [[0.4, 0], [0.9, 0]], [[0, 1], [0.8, 1]]]],
  ];
  const SAME = { I: 'l', l: 'I' };

  // ---------- recogniser ----------
  const N = 32;
  const flat = strokes => strokes.flatMap((s, id) => s.map(([x, y]) => ({ x, y, id })));
  function resample(strokes) {
    const pts = flat(strokes);
    let length = 0;
    for (let i = 1; i < pts.length; i++) if (pts[i].id === pts[i - 1].id) length += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    const step = length / (N - 1) || 1, out = [{ ...pts[0] }];
    let D = 0;
    for (let i = 1; i < pts.length; i++) {
      if (pts[i].id !== pts[i - 1].id) {
        // a new stroke: a dot or a short stroke still leaves a mark
        if (D > 0 || out.length < N) out.push({ ...pts[i] });
        D = 0;
        continue;
      }
      let a = pts[i - 1];
      const b = pts[i];
      let d = Math.hypot(b.x - a.x, b.y - a.y);
      while (D + d >= step && d > 0) {
        const t = (step - D) / d, q = { x: a.x + t * (b.x - a.x), y: a.y + t * (b.y - a.y), id: b.id };
        out.push(q);
        a = q;
        d = Math.hypot(b.x - a.x, b.y - a.y);
        D = 0;
      }
      D += d;
    }
    while (out.length < N) out.push({ ...pts[pts.length - 1] });
    // spread extra points evenly instead of cutting the last stroke short
    return out.length > N ? Array.from({ length: N }, (_, i) => out[Math.round((i * (out.length - 1)) / (N - 1))]) : out;
  }
  function normalize(points) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, cx = 0, cy = 0;
    for (const p of points) {
      minX = Math.min(minX, p.x), maxX = Math.max(maxX, p.x), minY = Math.min(minY, p.y), maxY = Math.max(maxY, p.y);
      cx += p.x / points.length, cy += p.y / points.length;
    }
    const size = Math.max(maxX - minX, maxY - minY) || 1;
    return points.map(p => ({ x: (p.x - cx) / size, y: (p.y - cy) / size }));
  }
  function cloudDistance(a, b, start) {
    const matched = new Array(N).fill(false);
    let sum = 0, i = start;
    do {
      let min = Infinity, index = -1;
      for (let j = 0; j < N; j++) {
        if (matched[j]) continue;
        const d = (a[i].x - b[j].x) ** 2 + (a[i].y - b[j].y) ** 2;
        if (d < min) (min = d), (index = j);
      }
      matched[index] = true;
      const weight = 1 - ((i - start + N) % N) / N;
      sum += weight * Math.sqrt(min);
      i = (i + 1) % N;
    } while (i !== start);
    return sum;
  }
  function shapeDistance(a, b) {
    const step = Math.floor(Math.sqrt(N));
    let best = Infinity;
    for (let i = 0; i < N; i += step) best = Math.min(best, cloudDistance(a, b, i), cloudDistance(b, a, i));
    return best;
  }
  const extent = strokes => {
    const ys = strokes.flat().map(p => p[1]);
    return { top: Math.min(...ys), bottom: Math.max(...ys) };
  };
  // FIT: the margin rule only helps a drawing that looks like the letter at all; past this score (a scribble that fits
// nothing well) the expected letter has to win outright.
  const TOLERANCE = 0.35, POSITION_WEIGHT = 2.2, MARGIN = 0.12, FIT = 2.5;
  const TEMPLATES = [...Object.entries(T), ...VARIANTS].map(([letter, strokes]) => ({
    letter, strokes, cloud: normalize(resample(strokes)), ...extent(strokes),
  }));
  function rank(strokes) {
    const drawn = strokes.filter(s => s.length);
    if (!drawn.length) return [];
    const cloud = normalize(resample(drawn)), { top, bottom } = extent(drawn);
    const best = {}, shapes = {};
    for (const t of TEMPLATES) {
      const off = Math.max(0, Math.abs(top - t.top) - TOLERANCE) + Math.max(0, Math.abs(bottom - t.bottom) - TOLERANCE);
      const shape = shapeDistance(cloud, t.cloud), score = shape + POSITION_WEIGHT * off;
      if (!(t.letter in best) || score < best[t.letter]) best[t.letter] = score;
      if (!(t.letter in shapes) || shape < shapes[t.letter]) shapes[t.letter] = shape;
    }
    // score: shape and position (what decides); shape: shape alone (used by diagnose to tell the two apart)
    return Object.entries(best).map(([letter, score]) => ({ letter, score, shape: shapes[letter] })).sort((a, b) => a.score - b.score);
  }
  // The letter a drawing reads as, given the letter that was asked for ('' for an empty box). ranked: rank(strokes).
  function read(strokes, expected, ranked = rank(strokes)) {
    if (!ranked.length) return '';
    const top = ranked[0].letter;
    if (top === expected || SAME[top] === expected) return expected;
    const mine = ranked.find(r => r.letter === expected);
    if (mine && mine.score <= FIT && mine.score <= ranked[0].score * (1 + MARGIN) + 0.05) return expected;
    return top;
  }

  // ---------- what went wrong (shown with the answer and kept in the mistake book) ----------
  const LINES = ['第一线', '第二线', '红线', '第四线'];
  const line = y => {
    const k = Math.round(y);
    if (Math.abs(y - k) < 0.2 && k >= 0 && k <= 3) return LINES[k];
    return ['第一格', '中间一格', '第三格'][Math.min(2, Math.max(0, Math.floor(y)))] + '里';
  };
  const named = c => (c === c.toUpperCase() ? '大写 ' : '小写 ') + c;
  const span = c => {
    const ys = T[c].flat().map(p => p[1]);
    return { top: Math.min(...ys), bottom: Math.max(...ys) };
  };
  const rule = c => {
    const { top, bottom } = span(c);
    if (top >= 0.8 && bottom <= 2.2) return `${named(c)} 只占中间一格（第二线到红线）`;
    return `${named(c)} 要从${line(top)}写到${line(bottom)}`;
  };
  // letters whose capital and small letter have the same shape: only the size tells them apart
  const SAME_SHAPE = new Set('cosvwxz');
  // the point that tells two easily confused letters apart (key: the two letters, sorted)
  const PAIRS = Object.fromEntries(Object.entries({
    bd: 'b 的半圆在竖线右边，d 的半圆在竖线左边。', pq: 'p 的半圆在竖线右边，q 的半圆在竖线左边。',
    bp: 'b 的竖线向上顶到第一线，p 的竖线向下写到第四线。', dq: 'd 的竖线向上顶到第一线，q 的竖线向下写到第四线。',
    bh: 'b 下面是封口的半圆，h 下面是开口的拱。', hn: 'h 的竖线要顶到第一线，n 只在中间一格。',
    nu: 'n 是上面拱起来，u 是下面弯过去。', mn: 'm 有两个拱，n 只有一个拱。', rn: 'r 只有一个小弯，n 的右边要写下来到红线。',
    uv: 'u 的底部是圆的，右边还有一条竖线；v 的底部是尖的。', vw: 'w 有两个尖，v 只有一个尖。', vy: 'y 的右边一笔要往下写到第四线。',
    ij: 'i 的竖线停在红线上，j 的竖线要往下写到第四线再向左弯。', il: 'i 上面有一个点，竖线只在中间一格；l 是一条长竖线。',
    lt: 't 中间有一横，上头比 l 短。', ft: 'f 顶到第一线并在上面向右弯，t 的上头短一点。', ce: 'e 中间有一条横线。',
    co: 'o 要封口，c 右边是开口的。', ao: 'a 右边还有一条竖线。', ad: 'd 的竖线要顶到第一线，a 只占中间一格。',
    ag: 'g 的尾巴要往下写到第四线再向左弯，a 只在中间一格。', gq: 'g 的尾巴向左弯，q 的尾巴是直的。', gy: 'g 上面是一个圆圈，y 上面是两条斜线。', hk: 'h 右边是拱，k 右边是两条斜线。',
    EF: 'E 有三横，F 底下没有横。', IL: 'L 底下还有一横。', PR: 'P 只有上面一个半圆，R 下面还有一条斜腿。', BP: 'B 有上下两个半圆，P 只有上面一个。',
    BR: 'B 下面是第二个半圆，R 下面是一条斜腿。', OQ: 'Q 比 O 多右下角一条小尾巴。', CG: 'G 比 C 多一小横，在右下方往里拐。',
    CO: 'O 要封口，C 右边是开口的。', DO: 'D 左边是一条直线。', UV: 'U 的底部是圆的，V 的底部是尖的。', MW: 'M 的尖朝上，W 的尖朝下。',
    MN: 'M 中间有两条斜线，N 只有一条。', HN: 'H 中间是一条横线，N 中间是一条斜线。', IT: 'T 上面有一横。', IJ: 'J 下面要向左弯。',
    VY: 'Y 的两条斜线在中间相交，下面是一条竖线。', EL: 'E 有三横，L 只有底下一横。', FP: 'P 上面是一个半圆，F 是两横。',
  }).map(([k, v]) => [[...k].sort().join(''), v]));

  // One or two sentences on what is wrong with a drawing of `expected` ('' when it is right).
  function diagnose(strokes, expected, ranked = rank(strokes)) {
    if (!ranked.length) return '格子是空的，没有写字母。';
    let got = read(strokes, expected, ranked);
    if (got === expected) return '';
    // I and l are one stroke: when the other one is the other case of the asked letter (asked L, wrote l), say that
    if (SAME[got] && SAME[got].toLowerCase() === expected.toLowerCase()) got = SAME[got];
    const drawn = extent(strokes.filter(s => s.length)), want = span(expected), look = '对照虚线里的标准写法再写一遍。';
    const where = () => {
      const parts = [];
      if (drawn.top < want.top - TOLERANCE) parts.push(`上面超过了${line(want.top)}`);
      else if (drawn.top > want.top + TOLERANCE) parts.push(`上面没有顶到${line(want.top)}`);
      if (drawn.bottom > want.bottom + TOLERANCE) parts.push(want.bottom === 2 ? '下面写到红线下面去了，应该落在红线上' : `下面超过了${line(want.bottom)}`);
      else if (drawn.bottom < want.bottom - TOLERANCE) parts.push(want.bottom >= 2.8 ? '尾巴要往下写到第四线' : `下面要写到${line(want.bottom)}`);
      return parts;
    };
    if (got && got.toLowerCase() === expected.toLowerCase()) {
      if (SAME_SHAPE.has(expected.toLowerCase())) {
        // same shape: the size is wrong (too big / too small) or the size is right but the letter sits in the wrong place
        const size = drawn.bottom - drawn.top, wantSize = want.bottom - want.top;
        if (size > wantSize + 0.5) return `写得太大了：${rule(expected)}。`;
        if (size < wantSize - 0.5) return `写得太小了：${rule(expected)}。`;
        return `位置不对：${where().join('，') || '没有对准四条线'}。${rule(expected)}。`;
      }
      return `你写的是${named(got)}，题目要的是${named(expected)}。`;
    }
    // the shape alone is right (it is the best shape, or close enough to it): then the place is what is wrong
    const byShape = [...ranked].sort((a, b) => a.shape - b.shape), mine = ranked.find(r => r.letter === expected);
    if (mine && mine.shape <= byShape[0].shape * (1 + MARGIN) + 0.05) {
      const parts = where();
      if (parts.length) return `形状对了，位置不对：${parts.join('，')}。${rule(expected)}。`;
      return '写得不够清楚，和别的字母分不开。' + look;
    }
    if (!got || got === '?') return '没认出这是哪个字母，笔画要连贯、写在格子中间。' + look;
    return `写出来更像${named(got)}。` + (PAIRS[[expected, got].sort().join('')] || look);
  }
  PK.handwriting = { rank, read, diagnose, templates: T };

  // ---------- four-line boxes ----------
  // Box geometry in CSS pixels: the four lines sit at TOP + k * gap (k = 0…3).
  const geometry = (w, h) => ({ top: h * 0.12, gap: (h * 0.76) / 3, left: w * 0.5 });
  function paint(canvas, strokes, opts = {}) {
    const ratio = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== Math.round(w * ratio)) (canvas.width = Math.round(w * ratio)), (canvas.height = Math.round(h * ratio));
    const g = canvas.getContext('2d'), { top, gap } = geometry(w, h);
    g.setTransform(ratio, 0, 0, ratio, 0, 0);
    g.clearRect(0, 0, w, h);
    for (let k = 0; k < 4; k++) {
      g.beginPath();
      g.strokeStyle = k === 2 ? '#e07a6a' : '#9db4cf';
      g.lineWidth = k === 2 ? 1.6 : 1.2;
      g.moveTo(0, top + k * gap);
      g.lineTo(w, top + k * gap);
      g.stroke();
    }
    const line = (list, color, width, dash) => {
      g.strokeStyle = color;
      g.lineWidth = width;
      g.lineCap = g.lineJoin = 'round';
      g.setLineDash(dash || []);
      for (const s of list) {
        g.beginPath();
        s.forEach(([x, y], i) => (i ? g.lineTo : g.moveTo).call(g, x * gap, top + y * gap));
        if (s.length === 1) g.lineTo(s[0][0] * gap + 0.1, top + s[0][1] * gap);
        g.stroke();
      }
      g.setLineDash([]);
    };
    if (opts.model) {
      // the model letter, centred in the box, drawn faintly behind the child's writing
      const xs = opts.model.flat().map(p => p[0]), mid = (Math.min(...xs) + Math.max(...xs)) / 2, shift = w / 2 / gap - mid;
      line(opts.model.map(s => s.map(([x, y]) => [x + shift, y])), '#b9c7b1', Math.max(3, gap * 0.12), [gap * 0.12, gap * 0.12]);
    }
    line(strokes, opts.color || '#2b4a7a', Math.max(2.5, gap * 0.09));
  }

  function attach(canvas, strokes, ctx) {
    let current = null;
    const point = e => {
      const r = canvas.getBoundingClientRect(), { top, gap } = geometry(r.width, r.height);
      return [+((e.clientX - r.left) / gap).toFixed(3), +((e.clientY - r.top - top) / gap).toFixed(3)];
    };
    canvas.addEventListener('pointerdown', e => {
      if (ctx.locked) return;
      e.preventDefault();
      canvas.setPointerCapture?.(e.pointerId);
      current = [point(e)];
      strokes.push(current);
      ctx.notice('');
      paint(canvas, strokes);
    });
    canvas.addEventListener('pointermove', e => {
      if (!current) return;
      e.preventDefault();
      for (const ev of e.getCoalescedEvents?.() || [e]) current.push(point(ev));
      paint(canvas, strokes);
    });
    const end = () => (current = null);
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);
    canvas.addEventListener('lostpointercapture', end);
  }

  const LABEL = { 1: ['写在这里'], 2: ['大写', '小写'] };
  PK.register('handwrite', {
    history: false,
    init(q, ctx) {
      ctx.view.boxes = [...q.answer].map(() => []);
      return { written: 0 };
    },
    render(q, a, ctx) {
      const wrap = el('div', 'q-write');
      wrap.append(el('p', 'prompt', q.prompt));
      if (q.shown) {
        const shown = el('p', 'write-shown', q.shown);
        shown.lang = 'en';
        wrap.append(shown);
      }
      const row = el('div', 'write-row');
      const value = ctx.result?.value || '';
      ctx.view.canvases = [];
      ctx.view.boxes.forEach((strokes, i) => {
        const cell = el('div', 'write-cell');
        const canvas = el('canvas', 'write-box');
        canvas.setAttribute('aria-label', `第${i + 1}格：${LABEL[q.answer.length][i]}`);
        canvas.setAttribute('role', 'img');
        if (ctx.result) cell.classList.add(value[i] === q.answer[i] ? 'right' : 'wrong');
        else attach(canvas, strokes, ctx);
        const clear = button('擦掉重写', () => {
          strokes.length = 0;
          paint(canvas, strokes);
        }, 'text-button write-clear');
        clear.disabled = ctx.locked;
        cell.append(el('span', 'write-label', LABEL[q.answer.length][i]), canvas, clear);
        row.append(cell);
        ctx.view.canvases.push(canvas);
      });
      wrap.append(row);
      return wrap;
    },
    afterRender(q, a, ctx) {
      const value = ctx.result?.value || '';
      ctx.view.canvases.forEach((canvas, i) => {
        const opts = ctx.result ? { model: T[q.answer[i]], color: value[i] === q.answer[i] ? '#3c8a52' : '#c2473a' } : {};
        const draw = () => paint(canvas, ctx.view.boxes[i], opts);
        draw();
        // The question may be built before it is on screen (0 px wide, so nothing gets drawn) and boxes change size
        // with 投屏 or a turned phone: repaint the lines whenever the box gets its real size.
        if (window.ResizeObserver) new ResizeObserver(draw).observe(canvas);
        else window.requestAnimationFrame?.(draw);
      });
    },
    value(q, a, ctx) {
      const boxes = ctx.view.boxes;
      if (boxes.some(b => !b.length)) return { incomplete: boxes.length > 1 ? '大写和小写两个格子都要写哦。' : '先在格子里写出字母。' };
      return { value: boxes.map((b, i) => read(b, q.answer[i]) || '?').join('') };
    },
    check: (q, value) => value === q.answer,
    diagnose: (q, value, ctx) => {
      const boxes = ctx.view.boxes, many = boxes.length > 1;
      return boxes.map((b, i) => {
        const why = diagnose(b, q.answer[i]);
        return why && (many ? `${LABEL[2][i]}：` : '') + why;
      }).filter(Boolean).join(' ');
    },
    solution: q => q.answer,
    describe: q => q.prompt + (q.shown ? `（${q.shown}）` : ''),
  });
})();
