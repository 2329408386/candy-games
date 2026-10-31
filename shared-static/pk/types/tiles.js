// Word tiles: q.kind "order" (被字句、反问句: tap cards to build the sentence), "delete" (缩写: tap cards to strike
// them out) or "expand" (扩写: pick a word, then the gap to insert it). Answers are compared with q.answers.
(() => {
  'use strict';
  const { el, button, shuffle } = PK;
  const chars = text => Array.from(text);

  function built(q, a) {
    if (q.kind === 'expand') {
      if (a.inserted === null) return null;
      const base = chars(q.baseSentence);
      return base.slice(0, a.position).join('') + a.inserted + base.slice(a.position).join('');
    }
    if (q.kind === 'delete') return q.cards.filter(c => !a.deleted.includes(c.id)).map(c => c.text).join('');
    return a.selection.map(id => q.cards.find(c => c.id === id).text).join('');
  }

  function expansion(q, a, ctx) {
    const prompt = el('p', 'prompt expand-sentence');
    const base = chars(q.baseSentence), choosing = a.option !== null && !ctx.locked;
    for (let i = 0; i <= base.length; i++) {
      if (choosing) {
        const gap = button('+', () => ctx.change(s => {
          s.inserted = s.option;
          s.position = i;
          s.option = null;
        }), 'insertion-point');
        gap.setAttribute('aria-label', i === 0 ? '插入到句首' : `插入到第${i}个字后`);
        prompt.append(gap);
      }
      if (a.inserted !== null && a.position === i) prompt.append(el('span', 'inserted-word', a.inserted));
      if (i < base.length) prompt.append(document.createTextNode(base[i]));
    }
    return prompt;
  }

  PK.register('tiles', {
    init(q, ctx) {
      ctx.view.cards = shuffle(q.cards || []);
      ctx.view.options = shuffle(q.options || []);
      return { selection: [], deleted: [], option: null, inserted: null, position: null };
    },
    render(q, a, ctx) {
      const wrap = el('div', 'q-tiles');
      if (q.kind === 'expand') {
        wrap.append(expansion(q, a, ctx));
        const options = el('div', 'options');
        for (const value of ctx.view.options) {
          const b = button(value, () => ctx.change(s => (s.option = value)), 'card' + (a.option === value ? ' selected' : ''));
          b.setAttribute('aria-pressed', String(a.option === value));
          options.append(b);
        }
        wrap.append(options);
        if (a.option !== null && !ctx.locked) wrap.append(el('p', 'muted', '点击句子中的“+”，把词语插进去。'));
      } else wrap.append(el('p', 'prompt', q.prompt));
      if (q.kind === 'order') {
        const line = el('div', 'answer-line');
        line.setAttribute('aria-label', '已排列的答案');
        if (!a.selection.length) line.append(el('span', 'muted', '依次点击下方词卡，组成句子'));
        for (const id of a.selection) {
          const card = q.cards.find(c => c.id === id);
          line.append(button(card.text, () => ctx.change(s => s.selection.splice(s.selection.indexOf(id), 1)), 'card selected'));
        }
        const tray = el('div', 'options');
        for (const card of ctx.view.cards) {
          const b = button(card.text, () => ctx.change(s => s.selection.push(card.id)));
          b.disabled = a.selection.includes(card.id);
          tray.append(b);
        }
        wrap.append(line, tray);
      }
      if (q.kind === 'delete') {
        const board = el('div', 'options sentence-cards');
        for (const card of q.cards) {
          const deleted = a.deleted.includes(card.id);
          const b = button(card.text, () => ctx.change(s => {
            const i = s.deleted.indexOf(card.id);
            if (i < 0) s.deleted.push(card.id);
            else s.deleted.splice(i, 1);
          }), 'card' + (deleted ? ' deleted' : ''));
          b.setAttribute('aria-pressed', String(deleted));
          b.setAttribute('aria-label', (deleted ? '已删去：' : '') + card.text);
          board.append(b);
        }
        wrap.append(board, el('p', 'preview', '缩写后：' + built(q, a)));
      }
      if (ctx.locked) wrap.querySelectorAll('button').forEach(b => (b.disabled = true));
      return wrap;
    },
    value(q, a, ctx) {
      const answer = built(q, a);
      const short = q.kind === 'order' && !ctx.ui.allowUnusedCards && a.selection.length !== (q.requiredCount || q.cards.length);
      if (q.kind === 'delete' && !a.deleted.length) return { incomplete: '先点词卡删去次要的部分，再提交。' };
      if (!answer || short) return { incomplete: q.kind === 'order' && !ctx.ui.allowUnusedCards ? '所有词卡都要用上，再提交。' : '请先完成答案，再提交。' };
      return { value: answer };
    },
    check: (q, value) => q.answers.includes(value),
    solution: q => q.answers.join('　或：'),
    describe: q => (q.kind === 'expand' ? `扩写：${q.baseSentence}` : q.prompt),
  });
})();
