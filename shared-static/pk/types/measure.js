// 量长度: read an illustrated object against a ruler and type the length, split over q.answerUnits
// (e.g. 厘米 + 毫米). Needs measure-objects.js (window.MathMeasureObjects) for the illustrations.
(() => {
  'use strict';
  const { el, button, svg } = PK;
  const FACTORS = { '毫米': 1, '厘米': 10, '分米': 100, '米': 1000, '千米': 1000000 };

  function ruler(q, p) {
    const width = 640, x0 = 30, w = 580, top = 40, X = mm => x0 + (mm / q.rulerLengthMm) * w;
    const object = MathMeasureObjects.draw(q, { xStart: X(q.startMm), xEnd: X(q.endMm), top, prefix: 'player-' + p });
    const objectBottom = top + object.height, y = objectBottom + 28, height = y + 65;
    let ticks = '';
    for (let mm = 0; mm <= q.rulerLengthMm; mm += q.minorEveryMm) {
      const major = mm % q.labelEveryMm === 0, x = X(mm);
      ticks += `<path d="M${x} ${y}v${major ? 24 : 12}" stroke="#62745e" stroke-width="${major ? 1.6 : 0.7}"/>`;
      if (major) ticks += `<text x="${x}" y="${y + 45}" text-anchor="middle" font-size="12">${mm / FACTORS[q.rulerLabelUnit]}</text>`;
    }
    return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${q.object}的实物插画及起点终点"><text x="320" y="22" text-anchor="middle" font-size="14">${q.object} · 尺面标数单位：${q.rulerLabelUnit}</text>` +
      `<path d="M${X(q.startMm)} ${top - 4}V${y}" stroke="#ad7632" stroke-dasharray="3 3"/><path d="M${X(q.endMm)} ${top - 4}V${y}" stroke="#ad7632" stroke-dasharray="3 3"/>${object.svg}` +
      `<text x="${Math.max(20, X(q.startMm))}" y="${objectBottom + 18}" text-anchor="middle" font-size="11">起点</text><text x="${Math.min(620, X(q.endMm))}" y="${objectBottom + 18}" text-anchor="middle" font-size="11">终点</text>` +
      `<rect x="${x0 - 8}" y="${y}" width="${w + 16}" height="53" rx="8" fill="#f4e6be"/>${ticks}</svg>`;
  }

  function zoom(point, label) {
    const from = Math.max(0, Math.floor(point / 10) * 10 - 10), w = 280, x0 = 15, X = mm => x0 + ((mm - from) / 30) * w;
    let ticks = '';
    for (let mm = from; mm <= from + 30; mm++) {
      const major = mm % 10 === 0;
      ticks += `<path d="M${X(mm)} 46v${major ? 22 : 12}" stroke="#62745e"/>`;
      if (major) ticks += `<text x="${X(mm)}" y="85" text-anchor="middle" font-size="13">${mm / 10}</text>`;
    }
    return `<svg viewBox="0 0 310 102" role="img" aria-label="${label}局部放大，每小格1毫米"><text x="155" y="18" text-anchor="middle" font-size="12">${label}放大 · 标数单位：厘米</text>` +
      `<rect x="7" y="46" width="296" height="45" rx="4" fill="#f4e6be"/>${ticks}<path d="M${X(point)} 25v43" stroke="#ae672e" stroke-width="2"/><circle cx="${X(point)}" cy="35" r="3" fill="#ae672e"/></svg>`;
  }

  PK.register('measure', {
    history: false,
    init: q => ({ parts: q.answerUnits.map(() => ''), active: 0 }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-measure');
      wrap.append(el('p', 'prompt', q.prompt),
        el('p', 'muted', q.variant === 'offset' ? '起点不是0：用终点刻度减去起点刻度。' : '起点对准0：读出物体右端的刻度。'),
        svg(ruler(q, ctx.player), 'measure-visual'));
      if (q.answerUnits.includes('毫米')) {
        const zooms = el('div', 'ruler-zooms');
        zooms.append(svg(zoom(q.startMm, '起点'), 'ruler-zoom'), svg(zoom(q.endMm, '终点'), 'ruler-zoom'));
        wrap.append(zooms, el('p', 'muted', '放大图每小格1毫米，与上方尺面是同一刻度。'));
      }
      const fields = el('div', 'answer-fields'), inputs = [];
      q.answerUnits.forEach((unit, index) => {
        const label = el('label', 'answer-part'), input = el('input');
        input.type = 'text';
        input.inputMode = 'numeric';
        input.pattern = '[0-9]*';
        input.maxLength = 5;
        input.value = a.parts[index];
        input.disabled = ctx.locked;
        input.setAttribute('aria-label', `${unit}数`);
        input.onfocus = () => {
          ctx.mutate(s => (s.active = index));
          inputs.forEach((n, i) => n.classList.toggle('active', i === index));
        };
        input.oninput = () => {
          input.value = input.value.replace(/\D/g, '').slice(0, 5);
          ctx.mutate(s => (s.parts[index] = input.value));
        };
        inputs.push(input);
        label.append(input, el('span', '', unit));
        fields.append(label);
      });
      inputs[a.active]?.classList.add('active');
      wrap.append(fields);
      const pad = el('div', 'keypad');
      for (const key of ['1', '2', '3', '4', '5', '6', '7', '8', '9', '清空', '0', '⌫']) {
        const b = button(key, () => ctx.mutate(s => {
          const i = s.active;
          s.parts[i] = key === '清空' ? '' : key === '⌫' ? s.parts[i].slice(0, -1) : (s.parts[i] + key).slice(0, 5);
          inputs[i].value = s.parts[i];
          inputs[i].focus();
        }), 'key');
        b.disabled = ctx.locked;
        pad.append(b);
      }
      wrap.append(pad);
      return wrap;
    },
    value: (q, a) => (a.parts.some(v => v === '') ? { incomplete: '请填写全部数字框，再提交。' } : { value: [...a.parts] }),
    check(q, value) {
      if (value.length !== q.answerParts.length || value.some(v => !/^\d{1,5}$/.test(v))) return false;
      const parts = value.map(Number);
      if (parts.length === 2 && parts[1] >= FACTORS[q.answerUnits[0]] / FACTORS[q.answerUnits[1]]) return false;
      return parts.every((v, i) => v === q.answerParts[i]);
    },
    solution: q => q.answer,
    describe: q => `${q.prompt}（起点${q.startMm}毫米，终点${q.endMm}毫米）`,
  });
})();
