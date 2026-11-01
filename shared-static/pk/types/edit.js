// 修改病句: pick a proofreading mark (改正/增补/删除/对调), then the word block or gap, then the word card.
// An answer is right when the edited sentence equals q.corrected or the result of another accepted edit, so any
// edit that gives one of those sentences counts (e.g. deleting either copy of a doubled word).
(() => {
  'use strict';
  const { el, button } = PK;
  const TOOLS = { replace: '改正号', insert: '增补号', delete: '删除号', swap: '对调号' };
  const TOOL_SHORT = { replace: '换', insert: '添', delete: '删', swap: '调' };
  const PATHS = {
    replace: '<rect x="4" y="36" width="36" height="18" rx="9"/><path d="M22 36V17H44"/><rect x="44" y="7" width="40" height="20" rx="10"/>',
    insert: '<path d="M9 51L19 39L29 51 M19 39V17H42"/><rect x="42" y="7" width="42" height="20" rx="10"/>',
    delete: '<rect x="8" y="34" width="44" height="20" rx="10"/><path d="M30 34C40 21 54 17 48 28C42 35 33 23 46 18C66 9 68 23 60 24C51 24 52 8 67 7L79 6"/>',
    swap: '<path d="M10 10V47H44V10H80V47"/>',
  };
  const HINTS = {
    none: '先点上面的修改符号。',
    insert: '点字词之间的短线，再选要添的字词。',
    swap: '依次点两个要交换位置的字词块。',
    delete: '点要去掉的字词块。',
    replace: '点要换的字词块，再选新的字词。',
  };
  const symbol = kind => `<svg viewBox="0 0 90 58" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" preserveAspectRatio="none">${PATHS[kind]}</svg>`;

  function starts(q) {
    let at = 0;
    return q.tokens.map(t => ((at += t.text.length), at - t.text.length));
  }
  function complete(e) {
    if (e.kind === 'replace') return !!(e.target && e.value);
    if (e.kind === 'insert') return e.position !== undefined && !!e.value;
    if (e.kind === 'delete') return !!e.target;
    return e.kind === 'swap' && !!e.first && !!e.second && e.first !== e.second;
  }
  // Mirrors apply_edit in tools/banks.py.
  function apply(q, e) {
    if (!complete(e)) return null;
    if (e.kind === 'insert') return q.original.slice(0, e.position) + e.value + q.original.slice(e.position);
    if (e.kind === 'swap') {
      const a = q.tokens.find(t => t.id === e.first), b = q.tokens.find(t => t.id === e.second);
      return q.tokens.map(t => (t.id === a.id ? b.text : t.id === b.id ? a.text : t.text)).join('');
    }
    return q.tokens.map(t => (t.id === e.target ? (e.kind === 'delete' ? '' : e.value) : t.text)).join('');
  }

  function addMark(wrap, kind, value) {
    if (kind === 'delete') {
      wrap.insertAdjacentHTML('beforeend', '<svg class="mark-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.5"><rect x="3" y="49" width="94" height="48" rx="24"/><path d="M48 49C59 30 75 25 68 40C61 52 52 32 69 25C90 13 93 33 83 34C72 34 77 9 93 9H99"/></svg>');
      return;
    }
    const outline = kind === 'replace' ? '<rect x="3" y="49" width="94" height="48" rx="24"/><path d="M25 49V16H50"/>' : '<path d="M10 90L50 55L90 90 M50 55V16H75"/>';
    wrap.insertAdjacentHTML('beforeend', `<svg class="mark-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.5">${outline}</svg>`);
    wrap.append(el('span', 'annotation correction-label', value || '　'));
  }

  function render(q, a, ctx) {
    // After a wrong answer the board shows the standard correction instead of the player's attempt.
    const e = ctx.result && !ctx.result.correct ? q.accepted[0] : a;
    const wrap = el('div', 'q-edit');
    const tools = el('div', 'symbol-tray');
    tools.setAttribute('aria-label', '修改符号');
    for (const kind of Object.keys(TOOLS)) {
      const b = button('', () => ctx.change(s => {
        for (const key of Object.keys(s)) delete s[key];
        s.kind = kind;
      }), 'symbol-button' + (e.kind === kind ? ' selected' : ''));
      b.innerHTML = symbol(kind) + `<span>${TOOL_SHORT[kind]}</span>`;
      b.setAttribute('aria-label', TOOLS[kind]);
      b.setAttribute('aria-pressed', String(e.kind === kind));
      b.disabled = ctx.locked;
      tools.append(b);
    }
    wrap.append(tools, el('p', 'edit-hint', ctx.locked ? '' : HINTS[a.kind || 'none']));
    const board = el('div', 'edit-board');
    const gap = position => {
      const holder = el('span', 'gap-wrap');
      const b = button('', () => ctx.change(s => {
        if (s.kind !== 'insert') return;
        s.position = position;
      }), 'gap' + (e.kind === 'insert' ? ' insertable' : ''));
      b.setAttribute('aria-label', `在第${position}字后插入`);
      b.disabled = ctx.locked || e.kind !== 'insert';
      holder.append(b);
      if (e.kind === 'insert' && e.position === position) {
        b.classList.add('selected');
        addMark(holder, 'insert', e.value);
      }
      board.append(holder);
    };
    const offsets = starts(q);
    q.tokens.forEach((token, i) => {
      gap(offsets[i]);
      const holder = el('span', 'word-wrap');
      const b = button(token.text, () => pickToken(ctx, a, token.id), 'word');
      b.dataset.token = token.id;
      b.disabled = ctx.locked;
      holder.append(b);
      if (e.target === token.id || e.first === token.id || e.second === token.id) {
        b.classList.add('selected');
        if (e.kind !== 'swap') addMark(holder, e.kind, e.value);
        else holder.append(el('span', 'annotation', e.first === token.id ? '①' : '②'));
      }
      board.append(holder);
    });
    gap(q.original.length);
    wrap.append(board);
    if (!ctx.locked && ((a.kind === 'replace' && a.target) || (a.kind === 'insert' && a.position !== undefined))) {
      const cards = el('div', 'word-cards');
      cards.setAttribute('aria-label', '可用字词');
      for (const value of ctx.view.cards) {
        cards.append(button(value, () => ctx.change(s => (s.value = value)), 'word-card' + (a.value === value ? ' selected' : '')));
      }
      wrap.append(cards);
    }
    const result = apply(q, e);
    if (result !== null) wrap.append(el('p', 'preview', (ctx.result && !ctx.result.correct ? '正确修改：' : '改后：') + result));
    return wrap;
  }

  function pickToken(ctx, a, id) {
    if (!a.kind) {
      ctx.notice('先点上面的修改符号，再点句中的位置。');
      return;
    }
    if (a.kind === 'insert') return;
    ctx.change(s => {
      if (s.kind === 'swap') {
        if (!s.first || s.second) {
          s.first = id;
          delete s.second;
        } else if (id !== s.first) s.second = id;
      } else {
        s.target = id;
        delete s.value;
      }
    });
  }

  function drawSwap(q, a, ctx, node) {
    const e = ctx.result && !ctx.result.correct ? q.accepted[0] : a;
    if (e.kind !== 'swap' || !e.first || !e.second) return;
    requestAnimationFrame(() => {
      const board = node.querySelector('.edit-board');
      const x = board?.querySelector(`[data-token="${e.first}"]`), y = board?.querySelector(`[data-token="${e.second}"]`);
      if (!x || !y) return;
      const r = board.getBoundingClientRect(), ar = x.getBoundingClientRect(), br = y.getBoundingClientRect();
      const left = ar.left <= br.left ? ar : br, right = ar.left <= br.left ? br : ar;
      const mid = (left.right + right.left) / 2 - r.left;
      const route = Math.abs(ar.top - br.top) < 10
        ? `M${left.left - r.left} ${left.top - r.top}V${left.bottom - r.top + 4}H${mid}V${right.top - r.top - 18}H${right.right - r.left}V${right.bottom - r.top + 4}`
        : `M${ar.left - r.left} ${ar.top - r.top}V${ar.bottom - r.top + 4}H4V${br.top - r.top - 18}H${br.right - r.left}V${br.bottom - r.top + 4}`;
      const lines = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      lines.classList.add('swap-lines');
      lines.setAttribute('aria-hidden', 'true');
      lines.innerHTML = `<path d="${route}" fill="none" stroke="currentColor" stroke-width="2"/>`;
      board.append(lines);
    });
  }

  PK.register('edit', {
    init(q, ctx) {
      ctx.view.cards = PK.shuffle(q.candidates);
      return {};
    },
    render,
    afterRender: drawSwap,
    value: (q, a) => (complete(a) ? { value: apply(q, a) } : { incomplete: '还没修改完成，请选好符号、位置和需要的字词。' }),
    // Any listed correction counts: q.corrected comes from accepted[0], the rest are other fixes that also keep the meaning.
    check: (q, value) => value === q.corrected || q.accepted.some(e => apply(q, e) === value),
    solution: q => q.corrected,
    describe: q => q.original,
  });
})();
