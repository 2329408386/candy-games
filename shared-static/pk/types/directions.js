// Campus maps for 校园方向PK, drawn above the choice options (q.figure = "direction-grid" | "direction-clock").
// The reference place has a blue frame and a "参照" tag, the target a yellow frame and a "目标" tag,
// so the map can be read without telling colours apart.
(() => {
  'use strict';
  const { el, svg } = PK;
  const REF = { fill: '#e5f0f7', stroke: '#6f95b0' }, TARGET = { fill: '#fff0c9', stroke: '#bb964b' };

  function gridMap(q) {
    const places = q.landmarks.map(a => ({ ...a, px: 65 + a.x * 105, py: 60 + a.y * 105 }));
    const boxes = places.map(a => {
      const ref = a.name === q.ref.name, target = a.name === q.target.name;
      const style = ref ? REF : target ? TARGET : { fill: '#f2f5eb', stroke: '#d6dfcd' };
      const tag = ref ? '参照' : target ? '目标' : '';
      return `<rect x="${a.px - 44}" y="${a.py - 22}" width="88" height="44" rx="10" fill="${style.fill}" stroke="${style.stroke}" stroke-width="${tag ? 2.5 : 1}"/>` +
        `<text x="${a.px}" y="${a.py + 5}" text-anchor="middle" font-size="14">${a.name}</text>` +
        (tag ? `<text x="${a.px}" y="${a.py - 27}" text-anchor="middle" font-size="11" fill="${style.stroke}">${tag}</text>` : '');
    });
    return `<svg viewBox="0 0 340 325" role="img" aria-label="校园示意图${q.mapId}，上北下南左西右东；参照地点${q.ref.name}，目标地点${q.target.name}">` +
      `<text x="170" y="18" text-anchor="middle" font-size="13">北 ↑</text>${boxes.join('')}</svg>`;
  }

  function clockMap(q, p) {
    const cx = 190, cy = 190;
    const at = (clock, r) => ({ x: cx + Math.sin((clock * Math.PI) / 6) * r, y: cy - Math.cos((clock * Math.PI) / 6) * r });
    const coords = a => (a.center ? { x: cx, y: cy } : at(a.clock, 112));
    const items = q.landmarks.map(a => {
      const pos = coords(a), ref = a.name === q.ref.name, target = a.name === q.target.name;
      const style = ref ? REF : target ? TARGET : { fill: '#f6f7f1', stroke: '#d8e1d0' };
      return `<rect x="${pos.x - 25}" y="${pos.y - 12}" width="50" height="24" rx="6" fill="${style.fill}" stroke="${style.stroke}"/>` +
        `<text x="${pos.x}" y="${pos.y + 5}" font-size="14" text-anchor="middle">${a.name}</text>`;
    }).join('');
    const ref = coords(q.ref), head = at(q.heading, 40), hx = ref.x + head.x - cx, hy = ref.y + head.y - cy;
    return `<svg viewBox="0 0 380 380" role="img" aria-label="校园示意图，观察者站在${q.ref.name}，面向正${q.headingLabel}">` +
      `<defs><marker id="face-arrow-${p}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 10 5 0 10z" fill="#6f95b0"/></marker></defs>` +
      `<circle cx="190" cy="190" r="160" fill="#f6f7ef" stroke="#dce4d4"/><text x="190" y="20" text-anchor="middle" font-size="13">北 ↑</text>` +
      `<text x="367" y="195" text-anchor="middle" font-size="12">东</text><text x="190" y="374" text-anchor="middle" font-size="12">南</text>` +
      `<text x="12" y="195" text-anchor="middle" font-size="12">西</text>${items}` +
      `<circle cx="190" cy="190" r="27" fill="${q.ref.center ? REF.fill : TARGET.fill}" stroke="#b7c9b1"/><text x="190" y="195" font-size="13" text-anchor="middle">操场</text>` +
      `<path d="M${ref.x} ${ref.y}L${hx} ${hy}" stroke="#6f95b0" stroke-width="2.5" marker-end="url(#face-arrow-${p})"/></svg>`;
  }

  PK.figures['direction-grid'] = q => {
    const wrap = el('div', 'figure');
    wrap.append(svg(gridMap(q), 'map-wrap'),
      el('p', 'figure-legend', `参照地点：${q.ref.name}（蓝框）；目标地点：${q.target.name}（黄框）。上北下南，左西右东。`));
    return wrap;
  };
  PK.figures['direction-clock'] = (q, ctx) => {
    const wrap = el('div', 'figure');
    wrap.append(svg(clockMap(q, ctx.player), 'map-wrap'),
      el('p', 'figure-legend', `你站在${q.ref.name}，箭头指向你的正前方（正${q.headingLabel}），这就是12点钟方向。`));
    return wrap;
  };
})();
