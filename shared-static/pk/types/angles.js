// 角的小侦探. Mode "turn": open or close a prop to make the asked angle, with a set-square to compare against.
// Mode "pick": tap every vertex of one kind on a polygon. Figure "angle-fig" draws q.fig for the choice questions.
// Third graders have not met degrees: angles are in degrees here only as data, and never shown on screen.
(() => {
  'use strict';
  const { el, button, svg } = PK;
  const NAME = { right: '直角', acute: '锐角', obtuse: '钝角' };
  const kindOf = deg => (deg === 90 ? 'right' : deg < 90 ? 'acute' : 'obtuse');
  const INK = '#3c4f47', ACCENT = '#c9792f', SOFT = '#ffe7b0';
  const polar = (cx, cy, r, deg) => [cx + r * Math.cos((deg * Math.PI) / 180), cy - r * Math.sin((deg * Math.PI) / 180)];
  const fix = n => Math.round(n * 10) / 10;

  // The angle itself: a soft sector at the vertex, and the little square when it is a right angle.
  function sector(cx, cy, from, deg, r = 34) {
    const [x1, y1] = polar(cx, cy, r, from), [x2, y2] = polar(cx, cy, r, from + deg);
    return `<path d="M${cx} ${cy}L${fix(x1)} ${fix(y1)}A${r} ${r} 0 ${deg > 180 ? 1 : 0} 0 ${fix(x2)} ${fix(y2)}Z" fill="${SOFT}" stroke="${ACCENT}"/>`;
  }
  function square(cx, cy, from, size = 16) {
    const a = polar(cx, cy, size, from), b = polar(a[0], a[1], size, from + 90), c = polar(cx, cy, size, from + 90);
    return `<path d="M${fix(a[0])} ${fix(a[1])}L${fix(b[0])} ${fix(b[1])}L${fix(c[0])} ${fix(c[1])}" fill="none" stroke="${ACCENT}" stroke-width="2"/>`;
  }
  function ray(cx, cy, len, deg, width = 3.5, color = INK) {
    const [x, y] = polar(cx, cy, len, deg);
    return `<path d="M${cx} ${cy}L${fix(x)} ${fix(y)}" stroke="${color}" stroke-width="${width}" stroke-linecap="round"/>`;
  }
  const plate = (cx, cy, r) => `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${INK}"/>`;

  // ---- props for mode "turn": base = direction of the fixed arm, moving arm = base + angle (counter-clockwise) ----
  const PROPS = {
    plain: (c, base, deg) => ray(c.x, c.y, 95, base) + ray(c.x, c.y, 95, base + deg) + plate(c.x, c.y, 5),
    scissors(c, base, deg) {
      const blade = d => {
        const [x, y] = polar(c.x, c.y, 100, d), [lx, ly] = polar(c.x, c.y, 16, d + 90), [rx, ry] = polar(c.x, c.y, 16, d - 90);
        const [hx, hy] = polar(c.x, c.y, 40, d + 180);
        return `<path d="M${fix(lx)} ${fix(ly)}L${fix(x)} ${fix(y)}L${fix(rx)} ${fix(ry)}Z" fill="#c8d4d6" stroke="${INK}" stroke-width="1.5"/>` +
          `<circle cx="${fix(hx)}" cy="${fix(hy)}" r="13" fill="none" stroke="#c35b4e" stroke-width="6"/>`;
      };
      return blade(base) + blade(base + deg) + plate(c.x, c.y, 5);
    },
    door(c, base, deg) {
      const [wx, wy] = polar(c.x, c.y, 100, base), [dx, dy] = polar(c.x, c.y, 95, base + deg), [kx, ky] = polar(c.x, c.y, 78, base + deg);
      return `<path d="M${c.x} ${c.y}L${fix(wx)} ${fix(wy)}" stroke="#8d7b64" stroke-width="10" stroke-linecap="round"/>` +
        `<path d="M${c.x} ${c.y}L${fix(dx)} ${fix(dy)}" stroke="#b37a44" stroke-width="9" stroke-linecap="round"/><circle cx="${fix(kx)}" cy="${fix(ky)}" r="4" fill="#ffe7b0"/>` + plate(c.x, c.y, 6);
    },
    fan(c, base, deg) {
      let ribs = '';
      for (let d = 0; d <= deg; d += 15) ribs += ray(c.x, c.y, 95, base + d, 1.5, '#a5774b');
      const [x1, y1] = polar(c.x, c.y, 95, base), [x2, y2] = polar(c.x, c.y, 95, base + deg);
      return `<path d="M${c.x} ${c.y}L${fix(x1)} ${fix(y1)}A95 95 0 0 0 ${fix(x2)} ${fix(y2)}Z" fill="#f4c9c0"/>` + ribs + ray(c.x, c.y, 97, base, 4, '#7d5531') + ray(c.x, c.y, 97, base + deg, 4, '#7d5531') + plate(c.x, c.y, 5);
    },
    clock(c, base, deg) {
      let ticks = '';
      for (let h = 0; h < 12; h++) {
        const [x1, y1] = polar(c.x, c.y, 92, 90 - h * 30), [x2, y2] = polar(c.x, c.y, h % 3 ? 86 : 80, 90 - h * 30);
        ticks += `<path d="M${fix(x1)} ${fix(y1)}L${fix(x2)} ${fix(y2)}" stroke="${INK}" stroke-width="${h % 3 ? 1.5 : 3}"/>`;
      }
      return `<circle cx="${c.x}" cy="${c.y}" r="98" fill="#fbf8ef" stroke="${INK}" stroke-width="3"/>${ticks}` + ray(c.x, c.y, 58, base, 5) + ray(c.x, c.y, 80, base + deg, 3.5) + plate(c.x, c.y, 5);
    },
    robot(c, base, deg) {
      const arm = d => { const [x, y] = polar(c.x, c.y, 92, d); return `<path d="M${c.x} ${c.y}L${fix(x)} ${fix(y)}" stroke="#6f95b0" stroke-width="13" stroke-linecap="round"/><circle cx="${fix(x)}" cy="${fix(y)}" r="8" fill="#ffd27a" stroke="${INK}"/>`; };
      return arm(base) + arm(base + deg) + `<circle cx="${c.x}" cy="${c.y}" r="12" fill="#ffd27a" stroke="${INK}" stroke-width="2"/>`;
    },
  };

  function setSquare(c, base) {
    const [ax, ay] = polar(c.x, c.y, 72, base), [bx, by] = polar(c.x, c.y, 72, base + 90);
    return `<path d="M${c.x} ${c.y}L${fix(ax)} ${fix(ay)}L${fix(bx)} ${fix(by)}Z" fill="#7fb6d6" fill-opacity=".38" stroke="#3d6886" stroke-width="2"/>` + square(c.x, c.y, base, 14).replaceAll(ACCENT, '#3d6886');
  }

  const inside = (q, deg) => kindOf(deg) === q.target;
  PK.modes.turn = {
    init: q => ({ angle: q.start, square: false }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-turn');
      wrap.append(el('p', 'prompt', q.prompt));
      const c = { x: 160, y: 125 }, showSquare = a.square || (ctx.result && q.target === 'right');
      const picture = `<svg viewBox="0 0 320 250" role="img" aria-label="${q.propName}，可以转动">${q.prop === 'fan' ? '' : sector(c.x, c.y, q.base, a.angle)}${PROPS[q.prop](c, q.base, a.angle)}${showSquare ? setSquare(c, q.base) : ''}${a.angle === 90 && ctx.result ? square(c.x, c.y, q.base) : ''}</svg>`;
      wrap.append(svg(picture, 'turn-pic'));
      if (ctx.locked) return wrap;
      const bar = el('div', 'turn-bar');
      const close = button('⟳ 合拢一点', () => ctx.change(s => (s.angle = Math.max(q.step, s.angle - q.step))), 'card turn-key');
      close.disabled = a.angle <= q.step;
      const open = button('⟲ 张开一点', () => ctx.change(s => (s.angle = Math.min(180 - q.step, s.angle + q.step))), 'card turn-key');
      open.disabled = a.angle >= 180 - q.step;
      bar.append(close, open, button(a.square ? '收起三角板' : '📐 拿三角板比一比', () => ctx.change(s => (s.square = !s.square)), 'card turn-key turn-tool'));
      wrap.append(bar);
      return wrap;
    },
    value: (q, a) => (a.angle === q.start ? { incomplete: '先转一转，再提交。' } : { value: a.angle }),
    check: (q, value) => inside(q, value),
    solution: q => ({ right: '转到和三角板的直角正好重合', acute: '转到比直角小', obtuse: '转到比直角大' }[q.target]),
    describe: q => q.prompt,
    diagnose: (q, value) => (inside(q, value) ? '' : `你转出的是${NAME[kindOf(value)]}，要的是${NAME[q.target]}。`),
  };

  // ---- polygons ----
  function fitPolygon(points, size = 240, pad = 44) {
    const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
    const minX = Math.min(...xs), minY = Math.min(...ys), w = Math.max(...xs) - minX || 1, h = Math.max(...ys) - minY || 1;
    const scale = (size - 2 * pad) / Math.max(w, h), ox = (size - w * scale) / 2, oy = (size - h * scale) / 2;
    return points.map(([x, y]) => [fix(ox + (x - minX) * scale), fix(oy + (y - minY) * scale)]);
  }
  // Right angles get their square from the bank's own coordinates (dot product zero), so the mark can never disagree.
  function polygonSvg(q, points, marks, label) {
    const pts = fitPolygon(points), n = pts.length;
    const centre = pts.reduce((c, p) => [c[0] + p[0] / n, c[1] + p[1] / n], [0, 0]);
    let out = `<polygon points="${pts.map(p => p.join(',')).join(' ')}" fill="#eef4e6" stroke="${INK}" stroke-width="3" stroke-linejoin="round"/>`;
    pts.forEach((p, i) => {
      const dx = p[0] - centre[0], dy = p[1] - centre[1], d = Math.hypot(dx, dy) || 1;
      const lx = fix(p[0] + (dx / d) * 21), ly = fix(p[1] + (dy / d) * 21);
      out += marks(i, p, lx, ly);
    });
    return `<svg viewBox="0 0 240 240" role="img" aria-label="${label}">${out}</svg>`;
  }

  const LETTERS = 'ABCDEFGH';
  PK.modes.pick = {
    init: () => ({ picked: [] }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-pick');
      wrap.append(el('p', 'prompt', q.prompt));
      const mark = (i, p, lx, ly) => {
        const on = a.picked.includes(i), right = q.answer.includes(i);
        const fill = ctx.result ? (right ? '#a7d59b' : on ? '#f0b09a' : '#fff') : on ? SOFT : '#fff';
        return `<g data-v="${i}" style="cursor:pointer"><circle cx="${lx}" cy="${ly}" r="15" fill="${fill}" stroke="${on ? ACCENT : INK}" stroke-width="2"/><text x="${lx}" y="${ly + 5}" text-anchor="middle" font-size="15" fill="${INK}">${LETTERS[i]}</text></g>`;
      };
      const pic = svg(polygonSvg(q, q.points, mark, q.shape), 'pick-pic');
      const toggle = i => ctx.change(s => {
        s.picked = s.picked.includes(i) ? s.picked.filter(v => v !== i) : [...s.picked, i].sort((x, y) => x - y);
      });
      if (!ctx.locked) pic.querySelectorAll('[data-v]').forEach(g => g.addEventListener('click', () => toggle(Number(g.dataset.v))));
      wrap.append(pic);
      const row = el('div', 'pick-row');
      q.points.forEach((_, i) => {
        const on = a.picked.includes(i);
        let cls = 'card pick-key' + (on ? ' selected' : '');
        if (ctx.result) cls = 'card pick-key' + (q.answer.includes(i) ? ' right' : on ? ' wrong' : '');
        const b = button('角 ' + LETTERS[i], () => toggle(i), cls);
        b.setAttribute('aria-pressed', String(on));
        b.disabled = ctx.locked;
        row.append(b);
      });
      wrap.append(row);
      return wrap;
    },
    value: (q, a) => (a.picked.length ? { value: [...a.picked] } : { incomplete: `点一点图上的字母，选出全部${NAME[q.target]}。` }),
    check: (q, value) => value.length === q.answer.length && q.answer.every(i => value.includes(i)),
    solution: q => `${NAME[q.target]}是 ${q.answer.map(i => '角' + LETTERS[i]).join('、')}`,
    describe: q => `${q.prompt}（${q.shape}）`,
    diagnose(q, value) {
      const extra = value.filter(i => !q.answer.includes(i)), miss = q.answer.filter(i => !value.includes(i));
      return [extra.length ? `多选了${extra.map(i => '角' + LETTERS[i]).join('、')}` : '', miss.length ? `漏了${miss.map(i => '角' + LETTERS[i]).join('、')}` : ''].filter(Boolean).join('，');
    },
  };

  // ---- figures for choice questions ----
  function angleSvg({ deg, rot, l1 = 90, l2 = 90 }, label) {
    const c = { x: 120, y: 100 };
    return `<svg viewBox="0 0 240 200" role="img" aria-label="${label || '一个角'}">${sector(c.x, c.y, rot, deg, 26)}${ray(c.x, c.y, l1, rot)}${ray(c.x, c.y, l2, rot + deg)}${plate(c.x, c.y, 4.5)}</svg>`;
  }
  function clockSvg(hour) {
    const c = { x: 110, y: 110 };
    let ticks = '';
    for (let h = 1; h <= 12; h++) {
      const [x, y] = polar(c.x, c.y, 80, 90 - h * 30);
      ticks += `<text x="${fix(x)}" y="${fix(y + 5)}" text-anchor="middle" font-size="14" fill="${INK}">${h}</text>`;
    }
    return `<svg viewBox="0 0 220 220" role="img" aria-label="钟面，${hour}时整"><circle cx="${c.x}" cy="${c.y}" r="100" fill="#fbf8ef" stroke="${INK}" stroke-width="3"/>${ticks}${ray(c.x, c.y, 45, 90 - hour * 30, 6)}${ray(c.x, c.y, 66, 90, 3.5)}${plate(c.x, c.y, 5)}</svg>`;
  }
  function raysSvg({ rays }) {
    const c = { x: 30, y: 170 };
    const labels = 'ABCDEF';
    let out = '';
    rays.forEach((d, i) => {
      const [x, y] = polar(c.x, c.y, 175, d), [tx, ty] = polar(c.x, c.y, 192, d);
      out += ray(c.x, c.y, 175, d, 3) + `<circle cx="${fix(x)}" cy="${fix(y)}" r="3" fill="${INK}"/><text x="${fix(tx)}" y="${fix(ty + 5)}" text-anchor="middle" font-size="14" fill="${INK}">${labels[i]}</text>`;
    });
    return `<svg viewBox="0 -28 240 222" role="img" aria-label="从一点引出${rays.length}条线">${out}${plate(c.x, c.y, 5)}<text x="${c.x - 12}" y="${c.y + 15}" font-size="14" fill="${INK}">O</text></svg>`;
  }
  const CUTS = {
    // The corner is cut along the dashed line; the shaded part falls away.
    'two-corners': { line: [[30, 30], [170, 170]], left: '30,30 170,170 30,170', off: '30,30 170,30 170,170' },
    'corner-side': { line: [[30, 30], [170, 100]], left: '30,30 170,100 170,170 30,170', off: '30,30 170,30 170,100' },
    'two-sides': { line: [[110, 30], [170, 90]], left: '30,30 110,30 170,90 170,170 30,170', off: '110,30 170,30 170,90' },
  };
  function cutSvg(cut) {
    const c = CUTS[cut];
    return `<svg viewBox="0 0 200 200" role="img" aria-label="正方形纸沿虚线剪去一部分"><polygon points="${c.off}" fill="#ece8dc" stroke="#b9b39f" stroke-dasharray="4 4"/><polygon points="${c.left}" fill="#d9e8f5" stroke="${INK}" stroke-width="2.5"/><path d="M${c.line[0]}L${c.line[1]}" stroke="${ACCENT}" stroke-width="2.5" stroke-dasharray="7 5"/></svg>`;
  }

  PK.figures['angle-fig'] = q => {
    const f = q.fig;
    if (f.kind === 'angle') return svg(angleSvg(f), 'angle-pic');
    if (f.kind === 'clock') return svg(clockSvg(f.hour), 'angle-pic');
    if (f.kind === 'rays') return svg(raysSvg(f), 'angle-pic');
    if (f.kind === 'cut') return svg(cutSvg(f.cut), 'angle-pic');
    if (f.kind === 'poly') return svg(polygonSvg(q, f.points, () => '', q.shape || '图形'), 'angle-pic');
    if (f.kind === 'compare') {
      const row = el('div', 'angle-pair');
      for (const [title, one] of [['角①', f.a], ['角②', f.b]]) {
        const box = el('div', 'angle-card');
        box.append(svg(angleSvg(one, title), 'angle-pic small'), el('p', 'wardrobe-title', title));
        row.append(box);
      }
      return row;
    }
    throw Error('未知的角图形：' + f.kind);
  };
})();
