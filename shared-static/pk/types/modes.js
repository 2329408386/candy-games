// Mixed question types for one game. A bank has one renderer type, so games that mix hands-on questions with
// picture choice use type "modes": q.mode names an entry of PK.modes (same contract as a renderer), and a question
// without mode is an ordinary choice question (types/choice.js, with PK.figures[q.figure] above the options).
// Also here: mode "fill", number boxes with an on-screen keypad (q.fields: [{before, answer, unit}], q.decimal adds the
// point key), which other modes reuse through PK.numberFields.
(() => {
  'use strict';
  const { el, button } = PK;
  PK.modes = {};
  const of = q => (q.mode ? PK.modes[q.mode] : PK.renderer('choice'));

  PK.register('modes', {
    history: false,
    init: (q, ctx) => of(q).init(q, ctx),
    render: (q, a, ctx) => of(q).render(q, a, ctx),
    value: (q, a, ctx) => of(q).value(q, a, ctx),
    check: (q, value, ctx) => of(q).check(q, value, ctx),
    solution: q => of(q).solution(q),
    describe: q => of(q).describe(q),
    diagnose: (q, value, ctx) => (of(q).diagnose ? of(q).diagnose(q, value, ctx) : ''),
  });

  // Number boxes and keypad over a.parts / a.active. fields: [{before, unit, answer}]; result colours each box.
  const MAX_DIGITS = 7;
  PK.numberFields = (fields, a, ctx, { decimal } = {}) => {
    const box = el('div', 'fill-area');
    const row = el('div', 'fill-row');
    fields.forEach((field, i) => {
      const group = el('span', 'fill-group');
      if (field.before) group.append(el('span', 'fill-text', field.before));
      let cls = 'fill-box' + (a.active === i && !ctx.locked ? ' active' : '');
      if (ctx.result) cls += sameNumber(a.parts[i], field.answer) ? ' right' : ' wrong';
      const b = button(a.parts[i] || '　', () => ctx.change(s => (s.active = i)), cls);
      b.setAttribute('aria-label', `第${i + 1}个空${a.parts[i] ? '，已填 ' + a.parts[i] : ''}`);
      b.disabled = ctx.locked;
      group.append(b);
      if (field.unit) group.append(el('span', 'fill-text', field.unit));
      row.append(group);
    });
    box.append(row);
    if (ctx.locked) return box;
    const pad = el('div', 'keypad fill-pad');
    const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', decimal ? '.' : '清空', '0', '⌫'];
    for (const key of keys) {
      pad.append(button(key, () => ctx.change(s => {
        const now = s.parts[s.active];
        if (key === '清空') s.parts[s.active] = '';
        else if (key === '⌫') s.parts[s.active] = now.slice(0, -1);
        else if (key === '.') { if (now && !now.includes('.')) s.parts[s.active] = now + '.'; }
        else if (now.length < MAX_DIGITS) s.parts[s.active] = now + key;
      }), 'key'));
    }
    box.append(pad);
    return box;
  };

  function sameNumber(typed, answer) {
    return /^\d+(\.\d+)?$/.test(String(typed)) && Number(typed) === Number(answer);
  }
  PK.sameNumber = sameNumber;

  PK.modes.fill = {
    init: q => ({ parts: q.fields.map(() => ''), active: 0 }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-fill');
      wrap.append(el('p', 'prompt', q.prompt));
      if (q.figure) wrap.append(PK.figures[q.figure](q, ctx));
      wrap.append(PK.numberFields(q.fields, a, ctx, { decimal: q.decimal }));
      return wrap;
    },
    value: (q, a) => (a.parts.some(v => !v) ? { incomplete: '请把每个空都填上，再提交。' } : { value: [...a.parts] }),
    check: (q, value) => value.length === q.fields.length && value.every((v, i) => sameNumber(v, q.fields[i].answer)),
    solution: q => q.answerText || q.fields.map(f => (f.before || '') + f.answer + (f.unit || '')).join(' '),
    describe: q => q.prompt,
  };
})();
