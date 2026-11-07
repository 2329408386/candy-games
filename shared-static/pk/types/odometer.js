// 里程表小司机. Mode "drive": a car leaves one stop on a line, tap the stop it reaches after q.km kilometres.
// Figures: "mileage-table" (train-style table: distance of each stop from the first), "route-line" (line with the km of
// each leg) and "dash" (odometer readings on a car's dashboard, q.readings: [{label, value}]).
(() => {
  'use strict';
  const { el, svg } = PK;
  const INK = '#3c4f47';

  function carIcon(x, y, flip) {
    return `<g class="car-move"><g transform="translate(${x} ${y}) ${flip ? 'scale(-1 1)' : ''}"><path d="M-18 0v-9l6-1 6-8h13l8 8 5 2v8z" fill="#e07a5f" stroke="${INK}" stroke-width="1.5"/><path d="M-5 -10l4-6h7l5 6z" fill="#dff0f7" stroke="${INK}"/><circle cx="-9" cy="1" r="4.5" fill="${INK}"/><circle cx="10" cy="1" r="4.5" fill="${INK}"/></g></g>`;
  }
  // Stops are evenly spaced, not to scale: three-letter names must stay readable on a phone. The legend says so.
  const stopX = (i, n) => 32 + (i * 356) / (n - 1);

  function lineSvg(stops, opts = {}) {
    const n = stops.length, y = 72;
    let out = `<path d="M${stopX(0, n)} ${y}H${stopX(n - 1, n)}" stroke="${INK}" stroke-width="5" stroke-linecap="round"/>`;
    stops.forEach((s, i) => {
      const x = stopX(i, n), on = opts.chosen === i, right = opts.answer === i;
      const fill = right ? '#a7d59b' : on ? (opts.answer === undefined ? '#ffd27a' : '#f0b09a') : '#fff';
      out += `<g data-stop="${i}" style="${opts.tappable ? 'cursor:pointer' : ''}"><rect x="${x - 34}" y="${y - 30}" width="68" height="78" fill="transparent"/><circle cx="${x}" cy="${y}" r="11" fill="${fill}" stroke="${INK}" stroke-width="3"/>` +
        `<text x="${x}" y="${y + 33}" text-anchor="middle" font-size="15" fill="${INK}">${s.name}</text></g>`;
      if (opts.legs && i < n - 1) out += `<text x="${(x + stopX(i + 1, n)) / 2}" y="${y - 14}" text-anchor="middle" font-size="14" fill="#a2542a">${stops[i + 1].km - s.km}千米</text>`;
    });
    if (opts.car !== undefined) out += carIcon(stopX(opts.car, n), y - 16, opts.flip);
    return `<svg viewBox="0 0 420 120" role="img" aria-label="线路：${stops.map(s => s.name).join('、')}">${out}</svg>`;
  }

  PK.modes.drive = {
    init: () => ({ stop: null }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-drive');
      wrap.append(el('p', 'prompt', q.prompt));
      const finished = !!ctx.result;
      const pic = svg(lineSvg(q.stops, { car: finished ? q.answer : q.from, flip: q.answer < q.from, chosen: a.stop, answer: finished ? q.answer : undefined, tappable: !ctx.locked }), 'route-line' + (finished ? ' drove' : ''));
      if (finished) pic.style.setProperty('--drive', `${stopX(q.from, q.stops.length) - stopX(q.answer, q.stops.length)}px`);
      if (!ctx.locked) pic.querySelectorAll('[data-stop]').forEach(g => g.addEventListener('click', () => ctx.change(s => (s.stop = Number(g.dataset.stop)))));
      wrap.append(pic);
      const table = PK.figures['mileage-table'](q);
      wrap.append(table);
      return wrap;
    },
    value: (q, a) => (a.stop === null ? { incomplete: '点线路上的车站，告诉小车停在哪里。' } : { value: a.stop }),
    check: (q, value) => value === q.answer,
    solution: q => q.stops[q.answer].name,
    describe: q => q.prompt,
    diagnose: (q, value) => `停在${q.stops[value]?.name ?? '？'}离${q.stops[q.from].name}有 ${Math.abs(q.stops[value].km - q.stops[q.from].km)} 千米。`,
  };

  // Cells wrap instead of a wide table, so every stop stays visible on a phone without scrolling sideways.
  PK.figures['mileage-table'] = q => {
    const box = el('div', 'mileage');
    box.append(el('p', 'wardrobe-title', q.tableTitle || `${q.stops[0].name}到各站的里程`));
    const grid = el('div', 'mileage-grid');
    for (const s of q.stops) {
      const cell = el('div', 'mileage-cell');
      cell.append(el('span', '', s.name), el('b', '', `${s.km}千米`));
      grid.append(cell);
    }
    box.append(grid);
    return box;
  };
  PK.figures['route-line'] = q => {
    const wrap = el('div', 'figure');
    wrap.append(svg(lineSvg(q.stops, { legs: true }), 'route-line'), el('p', 'figure-legend', '示意图，不按实际比例画。'));
    return wrap;
  };

  function rollers(value) {
    const row = el('span', 'rollers');
    row.setAttribute('aria-label', String(value));
    for (const d of String(value).padStart(5, '0')) row.append(el('b', 'roller', d));
    row.append(el('i', 'roller-unit', 'km'));
    return row;
  }
  PK.figures.dash = q => {
    const box = el('div', 'dash');
    for (const r of q.readings) {
      const line = el('div', 'dash-row');
      line.append(el('span', 'dash-label', r.label), rollers(r.value));
      box.append(line);
    }
    return box;
  };

})();
