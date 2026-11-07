// 小数变变变. Mode "pay": tap money to make exactly q.fen (the price in 分). The sum is hidden: the exercise is reading
// 6.85元 as 6元8角5分. Mode "stick": tap a position on a metre stick (q.metres long, marked in 分米).
// Figures: "stick-bug" (a ladybird at q.dm on the stick) and "gate" (a truck under a height limit).
(() => {
  'use strict';
  const { el, button, svg } = PK;
  const INK = '#3c4f47';
  const MONEY = [
    { fen: 1000, text: '10元', kind: 'note' }, { fen: 500, text: '5元', kind: 'note' }, { fen: 100, text: '1元', kind: 'note' },
    { fen: 50, text: '5角', kind: 'coin' }, { fen: 10, text: '1角', kind: 'coin' }, { fen: 5, text: '5分', kind: 'cent' }, { fen: 1, text: '1分', kind: 'cent' },
  ];
  const moneyOf = fen => MONEY.find(m => m.fen === fen);
  function yuanJiaoFen(fen) {
    const y = Math.floor(fen / 100), j = Math.floor(fen / 10) % 10, f = fen % 10;
    return [y ? `${y}元` : '', j ? `${j}角` : '', f ? `${f}分` : ''].join('') || '0元';
  }
  const piece = (m, onClick) => {
    const b = button(m.text, onClick, `money ${m.kind}`);
    b.setAttribute('aria-label', m.text);
    return b;
  };

  PK.modes.pay = {
    init: () => ({ tray: [] }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-pay');
      wrap.append(el('p', 'prompt', q.prompt));
      const tag = el('div', 'pay-tag');
      tag.append(el('span', 'goods-emoji', q.emoji), el('b', '', q.priceText));
      wrap.append(tag);
      const tray = el('div', 'pay-tray');
      tray.setAttribute('aria-label', '已经放上去的钱，点一下可以拿回来');
      if (!a.tray.length) tray.append(el('span', 'muted', '点下面的钱放到这里，放错了点这里的钱拿回来'));
      a.tray.forEach((fen, i) => {
        const p = piece(moneyOf(fen), () => ctx.change(s => s.tray.splice(i, 1)));
        p.disabled = ctx.locked;
        tray.append(p);
      });
      wrap.append(tray);
      if (ctx.locked) return wrap;
      const bank = el('div', 'pay-bank');
      for (const m of MONEY) bank.append(piece(m, () => ctx.change(s => s.tray.push(m.fen))));
      wrap.append(bank);
      return wrap;
    },
    value: (q, a) => (a.tray.length ? { value: a.tray.reduce((s, n) => s + n, 0) } : { incomplete: '先把钱放到托盘上。' }),
    check: (q, value) => value === q.fen,
    solution: q => `${q.priceText}＝${yuanJiaoFen(q.fen)}`,
    describe: q => q.prompt,
    diagnose: (q, value) => `你放了 ${yuanJiaoFen(value)}。`,
  };

  // ---- metre stick ----
  function stickSvg(q, opts = {}) {
    const n = q.metres * 10, x0 = 22, w = 376, X = i => x0 + (i * w) / n;
    let out = `<rect x="${x0 - 6}" y="46" width="${w + 12}" height="34" rx="6" fill="#f4e6be" stroke="#c9b477"/>`;
    for (let i = 0; i <= n; i++) {
      const major = i % 10 === 0, mid = i % 5 === 0;
      out += `<path d="M${X(i)} 46v${major ? 26 : mid ? 20 : 13}" stroke="#62745e" stroke-width="${major ? 2 : 1}"/>`;
      if (major) out += `<text x="${X(i)}" y="98" text-anchor="middle" font-size="14" fill="${INK}">${i / 10}米</text>`;
      if (opts.tappable) out += `<rect data-tick="${i}" x="${X(i) - w / n / 2}" y="20" width="${w / n}" height="66" fill="transparent" style="cursor:pointer"/>`;
    }
    const flag = (i, color) => `<path d="M${X(i)} 46V18" stroke="${color}" stroke-width="2.5"/><path d="M${X(i)} 18l16 6-16 6z" fill="${color}"/>`;
    if (opts.chosen !== undefined && opts.chosen !== null) out += flag(opts.chosen, opts.answer !== undefined && opts.chosen !== opts.answer ? '#c47a5b' : '#d9aa35');
    if (opts.answer !== undefined) out += flag(opts.answer, '#4d9148');
    if (opts.bug !== undefined) {
      const x = X(opts.bug);
      out += `<path d="M${x} 46V36" stroke="#b5433a" stroke-width="2"/><circle cx="${x}" cy="28" r="9" fill="#d65a4a" stroke="${INK}"/><path d="M${x} 19v18" stroke="${INK}"/><circle cx="${x - 4}" cy="26" r="1.6" fill="${INK}"/><circle cx="${x + 4}" cy="31" r="1.6" fill="${INK}"/>`;
    }
    return `<svg viewBox="0 0 420 108" role="img" aria-label="${q.metres}米长的尺子，每小格1分米">${out}</svg>`;
  }

  PK.modes.stick = {
    init: () => ({ tick: null }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-stick');
      wrap.append(el('p', 'prompt', q.prompt));
      const pic = svg(stickSvg(q, { tappable: !ctx.locked, chosen: a.tick, answer: ctx.result ? q.answer : undefined }), 'stick-pic');
      if (!ctx.locked) pic.querySelectorAll('[data-tick]').forEach(r => r.addEventListener('click', () => ctx.change(s => (s.tick = Number(r.dataset.tick)))));
      wrap.append(pic, el('p', 'figure-legend', '每小格是 1 分米，点在尺子上插小旗。'));
      if (!ctx.locked) {
        const row = el('div', 'stick-nudge');
        const move = d => ctx.change(s => (s.tick = Math.min(q.metres * 10, Math.max(0, (s.tick ?? 0) + d))));
        row.append(button('◀ 左移一格', () => move(-1), 'card turn-key'), button('右移一格 ▶', () => move(1), 'card turn-key'));
        wrap.append(row);
      }
      return wrap;
    },
    value: (q, a) => (a.tick === null ? { incomplete: '点尺子，把小旗插到对的位置。' } : { value: a.tick }),
    check: (q, value) => value === q.answer,
    solution: q => `${q.answer / 10}米＝${q.answer}分米（从0数${q.answer}小格）`,
    describe: q => q.prompt,
    diagnose: (q, value) => `小旗插在了 ${value / 10} 米。`,
  };
  PK.figures['stick-bug'] = q => svg(stickSvg(q, { bug: q.dm }), 'stick-pic');

  PK.figures.gate = q => {
    const scale = 55, ground = 170, x0 = 60;
    const h = v => ground - v * scale;
    const limit = q.gate.limit, truck = q.gate.truck, load = q.gate.load;
    const top = h(truck + load);
    return svg(`<svg viewBox="0 0 400 200" role="img" aria-label="限高${limit}米的杆子，货车高${truck}米，货物高${load}米">` +
      `<path d="M20 ${ground}H380" stroke="${INK}" stroke-width="3"/>` +
      `<path d="M280 ${ground}V${h(limit)}H370V${ground}" fill="none" stroke="#c35b4e" stroke-width="5"/>` +
      `<text x="325" y="${h(limit) - 8}" text-anchor="middle" font-size="14" fill="#a2433a">限高 ${limit} 米</text>` +
      `<rect x="${x0}" y="${h(truck)}" width="120" height="${ground - h(truck) - 12}" fill="#8aa9c6" stroke="${INK}" stroke-width="2"/>` +
      `<rect x="${x0 + 8}" y="${top}" width="104" height="${h(truck) - top}" fill="#e0b26b" stroke="${INK}" stroke-width="2"/>` +
      `<circle cx="${x0 + 28}" cy="${ground - 10}" r="10" fill="${INK}"/><circle cx="${x0 + 94}" cy="${ground - 10}" r="10" fill="${INK}"/>` +
      `<path d="M${x0 + 120} ${ground - 12}v-40h24l14 20v20z" fill="#b8cfe0" stroke="${INK}" stroke-width="2"/>` +
      `<text x="${x0 + 60}" y="${(h(truck) + ground) / 2}" text-anchor="middle" font-size="14" fill="#fff">车高 ${truck} 米</text>` +
      `<text x="${x0 + 60}" y="${(top + h(truck)) / 2 + 5}" text-anchor="middle" font-size="14" fill="${INK}">货高 ${load} 米</text></svg>`, 'gate-pic');
  };
})();
