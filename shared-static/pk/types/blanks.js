// Fill-in blanks from a fixed tray: 的地得 (one character per blank) and 标点 (several marks per blank).
// The text marks blanks as [0], [1], …; q.capacities gives the marks each blank takes (default 1), and
// q.acceptedAnswers lists every accepted set when a sentence allows more than one mood.
(() => {
  'use strict';
  const { el, button } = PK;
  const BLANK = /\[(\d+)\]/g;
  const capacity = (q, i) => (q.capacities ? q.capacities[i] : 1);
  const parts = q => q.text.split(BLANK); // even indexes: text, odd indexes: blank numbers
  const alternatives = q => q.acceptedAnswers || [q.answers];

  function render(q, a, ctx) {
    const wrap = el('div', 'q-blanks');
    const sentence = el('p', 'prompt blank-sentence');
    const expected = ctx.result && !ctx.result.correct ? q.answers : null;
    parts(q).forEach((part, i) => {
      if (i % 2 === 0) return sentence.append(document.createTextNode(part));
      const index = Number(part), fill = a.fills[index];
      let cls = 'blank' + (a.active === index && !ctx.locked ? ' active' : '');
      if (ctx.result) cls += alternatives(q).some(ans => ans[index] === fill) ? ' right' : ' wrong';
      const b = button(fill, () => ctx.change(s => (s.active = index)), cls);
      b.setAttribute('aria-label', `第${index + 1}个空${fill ? '，已填：' + fill : ''}`);
      b.disabled = ctx.locked;
      if (expected && expected[index] !== fill) {
        const fix = el('span', 'blank-fix', expected[index]);
        fix.setAttribute('aria-label', '正确：' + expected[index]);
        const holder = el('span', 'blank-holder');
        holder.append(b, fix);
        sentence.append(holder);
      } else sentence.append(b);
    });
    wrap.append(sentence);
    if (ctx.ui.hint && !ctx.locked) wrap.append(el('p', 'muted', ctx.ui.hint));
    const tray = el('div', 'options tray');
    tray.setAttribute('aria-label', '可选符号');
    for (const mark of ctx.ui.blankOptions) {
      const b = button(mark, () => put(q, a, ctx, mark), 'card tray-key');
      b.disabled = ctx.locked;
      tray.append(b);
    }
    const back = button('⌫ 删除', () => ctx.change(s => (s.fills[s.active] = Array.from(s.fills[s.active]).slice(0, -1).join(''))), 'card tray-key tray-back');
    back.disabled = ctx.locked;
    back.setAttribute('aria-label', '删除当前空格的最后一个符号');
    tray.append(back);
    wrap.append(tray);
    return wrap;
  }

  function put(q, a, ctx, mark) {
    const cap = capacity(q, a.active), now = Array.from(a.fills[a.active]);
    if (cap > 1 && now.length >= cap) {
      ctx.notice('这一格已经填满，请先删除，再修改。');
      return;
    }
    ctx.change(s => {
      s.fills[s.active] = cap === 1 ? mark : s.fills[s.active] + mark;
      // A full blank hands the cursor to the next blank that still has room.
      if (Array.from(s.fills[s.active]).length >= cap) {
        const next = s.fills.findIndex((f, i) => i > s.active && Array.from(f).length < capacity(q, i));
        if (next >= 0) s.active = next;
      }
    });
  }

  PK.register('blanks', {
    init: q => ({ fills: q.answers.map(() => ''), active: 0 }),
    render,
    value(q, a) {
      const missing = a.fills.findIndex((f, i) => Array.from(f).length !== capacity(q, i));
      if (missing >= 0) {
        a.active = missing;
        return { incomplete: q.capacities ? '请检查横线是否填写完整。' : '还有未填写的空格哦！' };
      }
      return { value: [...a.fills] };
    },
    check: (q, value) => alternatives(q).some(ans => ans.length === value.length && ans.every((v, i) => v === value[i])),
    solution: q => q.complete,
    describe: q => q.text.replace(BLANK, '（ ）'),
  });
})();
