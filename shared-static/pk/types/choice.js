// Single-answer choice: 课外阅读, 多音字 (highlighted character), 形近字 (blank in the sentence) and the
// choice-based math games. A question may name a figure (PK.figures[q.figure]) drawn above the options.
(() => {
  'use strict';
  const { el, button, shuffle } = PK;
  const LETTERS = 'ABCDEFGH';
  const lettered = (q, ctx) => ctx.ui.lettered && q.category !== 'judge';
  const optionText = (q, value, index, letter) => (letter ? LETTERS[index] + '. ' : '') + value + (q.optionSuffix || '');

  function promptNode(q, a) {
    const prompt = el('p', 'prompt');
    if (!q.focus) {
      prompt.textContent = q.prompt;
      return prompt;
    }
    const [start, length] = q.focus;
    prompt.append(document.createTextNode(q.prompt.slice(0, start)));
    if (q.blank) {
      const blank = el('span', 'letter-blank', a.choice ?? '');
      blank.setAttribute('aria-label', a.choice ? '已选择：' + a.choice : '待填写的汉字');
      prompt.append(blank);
    } else prompt.append(el('mark', '', q.prompt.slice(start, start + length)));
    prompt.append(document.createTextNode(q.prompt.slice(start + length)));
    return prompt;
  }

  PK.register('choice', {
    history: false,
    init(q, ctx) {
      // Lettered options (A. B. C.) keep the bank's order; everything else is shuffled per question.
      ctx.view.options = ctx.ui.lettered ? [...q.options] : shuffle(q.options);
      return { choice: null };
    },
    render(q, a, ctx) {
      const wrap = el('div', 'q-choice');
      wrap.append(promptNode(q, a));
      if (q.figure) wrap.append(PK.figures[q.figure](q, ctx));
      const letter = lettered(q, ctx);
      const options = el('div', 'options' + (letter ? ' options-list' : '') + (q.options.length > 6 ? ' options-many' : ''));
      options.setAttribute('role', 'group');
      options.setAttribute('aria-label', '选项');
      ctx.view.options.forEach((value, i) => {
        const chosen = String(a.choice) === String(value);
        let cls = 'card' + (chosen ? ' selected' : '');
        let text = optionText(q, value, i, letter);
        if (ctx.result) {
          if (String(value) === String(q.answer)) (cls += ' right'), (text += ' ✓');
          else if (chosen) (cls += ' wrong'), (text += ' ✗');
        }
        const b = button(text, () => ctx.change(s => (s.choice = value)), cls);
        b.setAttribute('aria-pressed', String(chosen));
        b.disabled = ctx.locked;
        options.append(b);
      });
      wrap.append(options);
      return wrap;
    },
    value: (q, a) => (a.choice === null ? { incomplete: '请先选择答案，再提交。' } : { value: a.choice }),
    check: (q, value) => String(value) === String(q.answer),
    solution(q) {
      if (q.answerText) return q.answerText;
      const index = q.options.findIndex(o => String(o) === String(q.answer));
      return q.category === 'choice' && index >= 0 && q.options.length > 2 ? LETTERS[index] + '. ' + q.answer : String(q.answer);
    },
    describe(q) {
      if (!q.focus) return q.prompt;
      const [start, length] = q.focus;
      const middle = q.prompt.slice(start, start + length);
      return q.prompt.slice(0, start) + (q.blank ? '（ ）' : `【${middle}】`) + q.prompt.slice(start + length);
    },
  });
})();
