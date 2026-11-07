// 小小搭配师. Mode "dress": try clothes on a paper doll and record every outfit, without repeats or gaps (q.tops, q.bottoms).
// Figures: "outfit-rows" (q.rows: rows of clothes or {emoji, name} items), "outfit-table" (a list recorded in order with
// one line missing, q.rows + q.table) and "route-map" (q.route: two stages with n1 and n2 roads).
(() => {
  'use strict';
  const { el, button, svg } = PK;
  let uid = 0;

  // A pattern as well as a colour, and the name beside it: clothes can be told apart without colour vision.
  function paint(item) {
    if (!item.pattern) return { defs: '', fill: item.color };
    const id = 'cloth-' + ++uid, base = `<rect width="12" height="12" fill="${item.color}"/>`;
    const mark = {
      stripes: '<rect width="12" height="5" fill="#fff" opacity=".65"/>',
      dots: '<circle cx="6" cy="6" r="2.6" fill="#fff" opacity=".8"/>',
      checks: '<rect width="6" height="6" fill="#fff" opacity=".5"/><rect x="6" y="6" width="6" height="6" fill="#fff" opacity=".5"/>',
    }[item.pattern];
    return { defs: `<defs><pattern id="${id}" width="12" height="12" patternUnits="userSpaceOnUse">${base}${mark}</pattern></defs>`, fill: `url(#${id})` };
  }
  const LINE = 'stroke="#51605a" stroke-width="1.6" stroke-linejoin="round"';
  const TOPS = {
    tee: f => `<path d="M42 66 26 80l9 11 8-6v30h34V85l8 6 9-11-16-14-10-3q-10 8-20 0z" fill="${f}" ${LINE}/>`,
    hoodie: f => `<path d="M42 66 25 82l-3 32 11 1 9-24v24h36V91l9 24 11-1-3-32-17-16-10-3q-10 8-20 0z" fill="${f}" ${LINE}/><path d="M49 63q11 12 22 0" fill="none" ${LINE}/>`,
    shirt: f => `<path d="M42 66 26 80l9 11 8-6v30h34V85l8 6 9-11-16-14-10-3-8 9-8-9z" fill="${f}" ${LINE}/><path d="M60 72v43" ${LINE}/><circle cx="64" cy="82" r="1.5" fill="#51605a"/><circle cx="64" cy="94" r="1.5" fill="#51605a"/>`,
  };
  const BOTTOMS = {
    pants: f => `<path d="M43 113h34l3 62H64l-4-44-4 44H40z" fill="${f}" ${LINE}/>`,
    shorts: f => `<path d="M43 113h34l4 28H63l-3-14-3 14H39z" fill="${f}" ${LINE}/>`,
    skirt: f => `<path d="M45 113h30l12 38H33z" fill="${f}" ${LINE}/>`,
  };
  const drawTop = (item, f) => TOPS[item.shape](f);
  const drawBottom = (item, f) => BOTTOMS[item.shape](f);

  function doll(top, bottom) {
    const pt = top && paint(top), pb = bottom && paint(bottom);
    const skin = '#f4d3b5';
    return `<svg viewBox="10 0 100 200" role="img" aria-label="${top ? top.name : '没穿上衣'}，${bottom ? bottom.name : '没穿下装'}">${pt?.defs || ''}${pb?.defs || ''}` +
      `<path d="M47 120h10v62H47zM63 120h10v62H63z" fill="${skin}"/><path d="M42 180h16v8H38zM62 180h16l4 8H62z" fill="#6c5a4b"/>` +
      `<path d="M38 72 26 110h9l9-30zM82 72l12 38h-9l-9-30z" fill="${skin}"/><rect x="53" y="50" width="14" height="18" fill="${skin}"/>` +
      (bottom ? drawBottom(bottom, pb.fill) : '') +
      (top ? drawTop(top, pt.fill) : '<path d="M43 66h34v50H43z" fill="#fbf8ef" stroke="#c8cfc2" stroke-dasharray="3 3"/>') +
      `<circle cx="60" cy="34" r="22" fill="${skin}"/><path d="M38 34q-2-24 22-24t22 24q-6-14-22-14t-22 14z" fill="#5a4636"/>` +
      `<circle cx="52" cy="36" r="2.4" fill="#3c3c3c"/><circle cx="68" cy="36" r="2.4" fill="#3c3c3c"/><path d="M54 45q6 5 12 0" fill="none" stroke="#b5654f" stroke-width="2" stroke-linecap="round"/></svg>`;
  }
  function piece(item, kind) {
    const p = paint(item);
    const box = kind === 'top' ? '18 56 84 66' : '28 108 64 72';
    return `<svg viewBox="${box}" role="img" aria-label="${item.name}">${p.defs}${kind === 'top' ? drawTop(item, p.fill) : drawBottom(item, p.fill)}</svg>`;
  }

  const key = (t, b) => `${t}-${b}`;
  const allKeys = q => q.tops.flatMap((_, t) => q.bottoms.map((_, b) => key(t, b)));
  const outfitName = (q, k) => {
    const [t, b] = k.split('-').map(Number);
    return `${q.tops[t].name}＋${q.bottoms[b].name}`;
  };

  function wardrobe(label, items, kind, chosen, pick, locked) {
    const box = el('div', 'wardrobe');
    box.append(el('p', 'wardrobe-title', label));
    const row = el('div', 'wardrobe-row');
    items.forEach((item, i) => {
      const b = button('', () => pick(i), 'wear' + (chosen === i ? ' selected' : ''));
      b.append(svg(piece(item, kind), 'wear-pic'), el('span', 'wear-name', item.name));
      b.setAttribute('aria-pressed', String(chosen === i));
      b.disabled = locked;
      row.append(b);
    });
    box.append(row);
    return box;
  }

  function resultGrid(q, found) {
    const table = el('table', 'dress-grid');
    const head = el('tr');
    head.append(el('th', '', '上衣＼' + q.bottomLabel));
    q.bottoms.forEach(b => head.append(el('th', '', b.name)));
    table.append(head);
    q.tops.forEach((t, ti) => {
      const row = el('tr');
      row.append(el('th', '', t.name));
      q.bottoms.forEach((_, bi) => {
        const ok = found.includes(key(ti, bi));
        row.append(el('td', ok ? 'found' : 'lost', ok ? '✓' : '漏了'));
      });
      table.append(row);
    });
    return table;
  }

  PK.modes.dress = {
    init: () => ({ top: null, bottom: null, found: [] }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-dress');
      wrap.append(el('p', 'prompt', q.prompt));
      const stage = el('div', 'dress-stage');
      const shelf = el('div', 'dress-shelf');
      shelf.append(
        wardrobe('上衣', q.tops, 'top', a.top, i => ctx.change(s => (s.top = i)), ctx.locked),
        wardrobe(q.bottomLabel, q.bottoms, 'bottom', a.bottom, i => ctx.change(s => (s.bottom = i)), ctx.locked));
      stage.append(svg(doll(q.tops[a.top], q.bottoms[a.bottom]), 'dress-doll'), shelf);
      wrap.append(stage);
      if (ctx.result) {
        wrap.append(resultGrid(q, ctx.result.value));
        return wrap;
      }
      const record = button(`📒 记下这一套（已记 ${a.found.length} 套）`, () => {
        if (a.top === null || a.bottom === null) return ctx.notice(`请先选一件上衣和一条${q.bottomLabel}。`);
        const k = key(a.top, a.bottom);
        if (a.found.includes(k)) return ctx.notice('这一套已经记过了，换一种搭配吧！');
        ctx.change(s => s.found.push(k));
      }, 'dress-record');
      wrap.append(record);
      const list = el('ol', 'dress-list');
      for (const k of a.found) list.append(el('li', '', outfitName(q, k)));
      wrap.append(list, el('p', 'muted', '都找全了，再点“提交答案”。'));
      return wrap;
    },
    value: (q, a) => (a.found.length ? { value: [...a.found].sort() } : { incomplete: '先给娃娃穿上一套，再点“记下这一套”。' }),
    check(q, value) {
      const all = allKeys(q);
      return value.length === all.length && all.every(k => value.includes(k));
    },
    solution: q => `一共 ${q.tops.length * q.bottoms.length} 套：${q.tops.length}×${q.bottoms.length}＝${q.tops.length * q.bottoms.length}`,
    describe: q => q.prompt,
    diagnose(q, value) {
      const missing = allKeys(q).filter(k => !value.includes(k));
      return missing.length ? `还差 ${missing.length} 套：${missing.map(k => outfitName(q, k)).join('、')}` : '';
    },
  };

  function rowsNode(q) {
    const box = el('div', 'item-rows');
    for (const row of q.rows) {
      box.append(el('p', 'wardrobe-title', `${row.label}（${row.items.length}）`));
      const line = el('div', 'wardrobe-row');
      for (const item of row.items) {
        const card = el('div', 'wear still');
        if (item.emoji) card.append(el('span', 'wear-emoji', item.emoji));
        else card.append(svg(piece(item, row.kind), 'wear-pic'));
        card.append(el('span', 'wear-name', item.name));
        line.append(card);
      }
      box.append(line);
    }
    return box;
  }
  PK.figures['outfit-rows'] = q => rowsNode(q);
  PK.figures['outfit-table'] = q => {
    const box = el('div', 'item-rows');
    box.append(rowsNode(q));
    const list = el('ol', 'dress-list record');
    for (const line of q.table) list.append(el('li', '', line));
    box.append(el('p', 'wardrobe-title', '记录本'), list);
    return box;
  };

  function roads(x1, x2, n, y, color) {
    let d = '';
    for (let i = 0; i < n; i++) {
      const bend = (i - (n - 1) / 2) * 42, mid = (x1 + x2) / 2;
      d += `<path d="M${x1} ${y}Q${mid} ${y + bend * 1.7} ${x2} ${y}" fill="none" stroke="${color}" stroke-width="4" stroke-linecap="round"/>` +
        `<text x="${mid}" y="${y + bend * 0.85 + 5}" text-anchor="middle" font-size="13" fill="#3c4f47">${'①②③④⑤'[i]}</text>`;
    }
    return d;
  }
  PK.figures['route-map'] = q => {
    const { a, b, c, n1, n2 } = q.route, y = 95;
    const place = (x, name) => `<circle cx="${x}" cy="${y}" r="27" fill="#fff5d6" stroke="#bb964b" stroke-width="2"/><text x="${x}" y="${y + 5}" text-anchor="middle" font-size="14" fill="#3c4f47">${name}</text>`;
    return svg(`<svg viewBox="0 0 420 190" role="img" aria-label="从${a}到${b}有${n1}条路，从${b}到${c}有${n2}条路">` +
      `${roads(45, 210, n1, y, '#8db58a')}${roads(210, 375, n2, y, '#8aa9c6')}${place(45, a)}${place(210, b)}${place(375, c)}</svg>`, 'route-map');
  };
})();
