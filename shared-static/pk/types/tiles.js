// Word tiles: q.kind "order" (被字句、反问句、英语连词成句: tap cards to build the sentence) or "keep" (缩写: the sentence
// is laid out as cards in order; tap the ones to keep). Answers are compared with q.answers. ui.joiner (" " for English)
// goes between the cards; q.tail is closing punctuation that is not a card.
// (扩写 has its own renderer, types/expand.js.)
(() => {
  'use strict';
  const { el, button, shuffle } = PK;

  function built(q, a, ctx) {
    const joiner = ctx?.ui?.joiner || '';
    const texts = q.kind === 'keep' ? q.cards.filter(c => a.kept.includes(c.id)).map(c => c.text)
      : a.selection.map(id => q.cards.find(c => c.id === id).text);
    const text = texts.join(joiner);
    return text && text + (q.tail || ''); // the closing punctuation is not a card
  }

  PK.register('tiles', {
    init(q, ctx) {
      ctx.view.cards = shuffle(q.cards || []);
      ctx.view.options = shuffle(q.options || []);
      return { selection: [], kept: [] };
    },
    render(q, a, ctx) {
      const wrap = el('div', 'q-tiles');
      wrap.append(el('p', 'prompt', q.prompt));
      if (q.kind === 'order') {
        const line = el('div', 'answer-line');
        line.setAttribute('aria-label', '已排列的答案');
        if (!a.selection.length) line.append(el('span', 'muted', '依次点击下方词卡，组成句子'));
        for (const id of a.selection) {
          const card = q.cards.find(c => c.id === id);
          line.append(button(card.text, () => ctx.change(s => s.selection.splice(s.selection.indexOf(id), 1)), 'card selected'));
        }
        if (a.selection.length && q.tail) line.append(el('span', 'tail', q.tail));
        const tray = el('div', 'options');
        for (const card of ctx.view.cards) {
          const b = button(card.text, () => ctx.change(s => s.selection.push(card.id)));
          b.disabled = a.selection.includes(card.id);
          tray.append(b);
        }
        wrap.append(line, tray);
      }
      if (q.kind === 'keep') {
        const board = el('div', 'options sentence-cards');
        for (const card of q.cards) {
          const kept = a.kept.includes(card.id);
          const b = button(card.text, () => ctx.change(s => {
            const i = s.kept.indexOf(card.id);
            if (i < 0) s.kept.push(card.id);
            else s.kept.splice(i, 1);
          }), 'card' + (kept ? ' kept' : ''));
          b.setAttribute('aria-pressed', String(kept));
          b.setAttribute('aria-label', (kept ? '已留下：' : '') + card.text);
          board.append(b);
        }
        wrap.append(board, el('p', 'preview', '缩写后：' + (built(q, a, ctx) || '（还没有选）')));
      }
      if (ctx.locked) wrap.querySelectorAll('button').forEach(b => (b.disabled = true));
      return wrap;
    },
    value(q, a, ctx) {
      const answer = built(q, a, ctx);
      const short = q.kind === 'order' && !ctx.ui.allowUnusedCards && a.selection.length !== (q.requiredCount || q.cards.length);
      if (q.kind === 'keep' && !a.kept.length) return { incomplete: '先点选要留下的词语，再提交。' };
      if (!answer || short) return { incomplete: q.kind === 'order' && !ctx.ui.allowUnusedCards ? '所有词卡都要用上，再提交。' : '请先完成答案，再提交。' };
      return { value: answer };
    },
    check: (q, value) => q.answers.includes(value),
    solution: q => q.answers.join('　或：'),
    describe: q => q.prompt,
  });
})();
