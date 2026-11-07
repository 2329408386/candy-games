// Grading checks for the shared renderers in shared-static/pk/types, run against the real question banks.
// Each renderer is loaded with a stub PK that only records PK.register(); no DOM is needed for check().
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.resolve(__dirname, '..');
const renderers = {};
const PK = { register: (type, r) => (renderers[type] = r), renderer: type => renderers[type], el() {}, button() {}, svg() {}, shuffle: a => [...a], figures: {} };
for (const file of ['choice', 'blanks', 'tiles', 'expand', 'edit', 'measure', 'modes', 'outfit', 'angles', 'odometer', 'shop', 'decimals']) {
  vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'shared-static/pk/types', file + '.js'), 'utf8'), { PK, window: {} });
}
const bank = (site, game) => JSON.parse(fs.readFileSync(path.join(ROOT, 'sites', site, 'banks', game + '.json'), 'utf8'));
const apply = (q, e) => renderers.edit.value(q, e).value;

// 修改病句: every accepted edit is graded right, and deleting either copy of a doubled word counts.
const sentence = bank('chinese', 'sentence');
for (const q of sentence.questions) {
  for (const e of q.accepted) assert.ok(renderers.edit.check(q, apply(q, e)), `${q.id} accepted edit graded wrong`);
  const d = q.accepted.find(e => e.kind === 'delete');
  const i = d && q.tokens.findIndex(t => t.id === d.target);
  for (const j of d ? [i - 1, i + 1] : []) {
    const twin = q.tokens[j];
    if (!twin || twin.text !== q.tokens[i].text) continue;
    assert.ok(renderers.edit.check(q, apply(q, { kind: 'delete', target: twin.id })), `${q.id} deleting the other copy graded wrong`);
  }
  assert.ok(!renderers.edit.check(q, q.original), `${q.id} unchanged sentence graded right`);
}

// Tiles: every listed answer is right; 缩写 needs at least one kept card, and keeping every card is wrong.
for (const game of ['passive', 'rhetorical', 'shorten']) {
  for (const q of bank('chinese', game).questions) for (const ans of q.answers) assert.ok(renderers.tiles.check(q, ans), `${game} ${q.id}`);
}
for (const q of bank('chinese', 'shorten').questions) {
  assert.ok(renderers.tiles.value(q, { kept: [] }, { ui: {} }).incomplete, `${q.id} untouched 缩写 accepted`);
  const all = renderers.tiles.value(q, { kept: q.cards.map(c => c.id) }, { ui: {} }).value;
  assert.strictEqual(all, q.prompt, `${q.id} keeping every card should rebuild the sentence`);
  assert.ok(!renderers.tiles.check(q, all), `${q.id} unshortened sentence graded right`);
}

// 扩写: inserting each slot's first word at its first position is right; fewer insertions are incomplete;
// moving a word to the wrong gap or using a distractor is wrong.
for (const q of bank('chinese', 'expand').questions) {
  const ins = {};
  for (const s of q.slots) ins[s.positions[0]] = s.words[0];
  const right = renderers.expand.value(q, { ins });
  assert.ok(renderers.expand.check(q, right.value), `${q.id} standard expansion graded wrong`);
  const one = { [q.slots[0].positions[0]]: q.slots[0].words[0] };
  assert.ok(renderers.expand.value(q, { ins: one }).incomplete, `${q.id} single insertion accepted`);
  const words = new Set(q.slots.flatMap(s => s.words));
  const decoy = q.cards.find(c => !words.has(c));
  const wrong = { ...ins, [q.slots[0].positions[0]]: decoy };
  assert.ok(!renderers.expand.check(q, renderers.expand.value(q, { ins: wrong }).value), `${q.id} distractor graded right`);
}

// Blanks: the key and every accepted alternative are right; a wrong mark is not.
for (const game of ['dedide', 'punctuation']) {
  for (const q of bank('chinese', game).questions) {
    for (const ans of q.acceptedAnswers || [q.answers]) assert.ok(renderers.blanks.check(q, ans), `${game} ${q.id}`);
    assert.ok(!renderers.blanks.check(q, q.answers.map(() => '？？').slice()), `${game} ${q.id} wrong answer graded right`);
  }
}

// Measure: the stored parts are right, a part that should carry into the larger unit is not.
for (const q of bank('math', 'measure').questions) {
  assert.ok(renderers.measure.check(q, q.answerParts.map(String)), q.id);
  if (q.answerParts.length === 2) {
    const ratio = { 毫米: 1, 厘米: 10, 分米: 100, 米: 1000 };
    const r = ratio[q.answerUnits[0]] / ratio[q.answerUnits[1]];
    assert.ok(!renderers.measure.check(q, [String(q.answerParts[0] - 1), String(q.answerParts[1] + r)]), `${q.id} uncarried`);
  }
}
// Choice: the key is right, every other option is wrong.
for (const [site, games] of [['chinese', ['reading', 'similar-char', 'polyphone']], ['math', ['conversion', 'mental', 'units', 'directions']],
  ['english', ['listen-word', 'scene']]]) {
  for (const game of games) {
    for (const q of bank(site, game).questions) {
      for (const o of q.options) assert.strictEqual(renderers.choice.check(q, o), String(o) === String(q.answer), `${game} ${q.id} ${o}`);
    }
  }
}

// English pictures: every picture a bank names is drawn by types/english.js (plain text counts only for emoji).
vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'shared-static/pk/types/english.js'), 'utf8'), { PK, window: {} });
const known = name => PK.pictureKeys.has(name) || !/[A-Za-z]/.test(name);
for (const game of ['listen-word', 'scene']) {
  for (const q of bank('english', game).questions) {
    if (q.pictures) for (const o of q.options) assert.ok(PK.pictureKeys.has(o), `${game} ${q.id}: no picture for option ${o}`);
    for (const item of q.pic || []) {
      for (const name of typeof item === 'object' ? [item.item, item.at] : [item]) assert.ok(known(name), `${game} ${q.id}: no picture ${name}`);
    }
  }
}

// 句子小侦探: one blank, three options, only the bank's answer is graded right, and it moves through A, B and C.
const grammar = bank('english', 'grammar');
for (const q of grammar.questions) {
  assert.strictEqual(q.prompt.split('___').length, 2, `${q.id} should have one blank`);
  assert.strictEqual(q.options.length, 3, q.id);
  for (const o of q.options) assert.strictEqual(renderers.choice.check(q, o), o === q.answer, `${q.id} ${o}`);
}
assert.strictEqual(new Set(grammar.questions.map(q => q.options.indexOf(q.answer))).size, 3, '句子小侦探 answers should move through A, B, C');

// 读句子: a reading counts from 80 points; an unread sentence is incomplete.
vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'shared-static/pk/types/read.js'), 'utf8'), { PK, window: {} });
for (const q of bank('english', 'read').questions) {
  assert.ok(renderers.read.value(q, { score: null }).incomplete, q.id);
  assert.ok(renderers.read.check(q, renderers.read.value(q, { score: 80, heard: '' }).value), q.id);
  assert.ok(!renderers.read.check(q, renderers.read.value(q, { score: 79, heard: '' }).value), q.id);
}
// 数学 B 组 (type "modes"): each mode grades its stored answer right and the typical slip wrong.
const modes = renderers.modes;
const right = q => {
  if (!q.mode) return q.answer;
  if (q.mode === 'fill') return q.fields.map(f => f.answer);
  if (q.mode === 'dress') return q.tops.flatMap((_, t) => q.bottoms.map((_, b) => `${t}-${b}`));
  if (q.mode === 'turn') return { right: 90, acute: 60, obtuse: 120 }[q.target];
  if (q.mode === 'pick' || q.mode === 'drive' || q.mode === 'stick') return q.answer;
  if (q.mode === 'cart') return { cart: q.items.map((_, i) => q.order.find(o => o.item === i)?.count ?? 0), total: q.total };
  if (q.mode === 'pay') return q.fen;
  throw Error('unknown mode ' + q.mode);
};
for (const game of ['outfit', 'angles', 'odometer', 'shop', 'decimals']) {
  const b = bank('math', game);
  assert.strictEqual(b.type, 'modes', game);
  for (const q of b.questions) {
    assert.ok(modes.check(q, right(q)), `${q.id} right answer graded wrong`);
    assert.ok(modes.solution(q), `${q.id} has no solution text`);
    assert.ok(modes.describe(q), `${q.id} has no description`);
    if (!q.mode) for (const o of q.options) assert.strictEqual(modes.check(q, o), String(o) === String(q.answer), `${q.id} option ${o}`);
    if (q.mode === 'fill') assert.ok(!modes.check(q, q.fields.map(f => String(Number(f.answer) + 1))), `${q.id} off by one graded right`);
    if (q.mode === 'dress') {
      const missing = right(q).slice(1);
      assert.ok(!modes.check(q, missing), `${q.id} missing outfit graded right`);
      assert.ok(modes.diagnose(q, missing).includes('还差 1 套'), `${q.id} diagnose`);
    }
    if (q.mode === 'turn') assert.ok(!modes.check(q, q.target === 'right' ? 90 + q.step : 90), `${q.id} near miss graded right`);
    if (q.mode === 'pick') {
      assert.ok(!modes.check(q, q.answer.slice(1)), `${q.id} partial pick graded right`);
      assert.ok(!modes.check(q, [...q.answer, 99]), `${q.id} extra pick graded right`);
    }
    if (q.mode === 'cart') {
      const v = right(q);
      const more = { ...v, cart: v.cart.map((n, i) => (i === q.order[0].item ? n + 1 : n)) };
      assert.ok(!modes.check(q, more), `${q.id} one extra item graded right`);
      assert.ok(modes.diagnose(q, more).includes('和单子不一样'), `${q.id} diagnose`);
      assert.ok(!modes.check(q, { ...v, total: String(Number(v.total) + 1) }), `${q.id} wrong total graded right`);
    }
    if (q.mode === 'pay') assert.ok(!modes.check(q, q.fen + 45), `${q.id} 1.05 read as 1.5 graded right`);
  }
}
// Typed decimals compare as numbers: 1.30 is 1.3, but 1.05 is not 1.5 and half-typed numbers are wrong.
const decimalQ = { mode: 'fill', fields: [{ answer: '1.3' }] };
assert.ok(modes.check(decimalQ, ['1.30']));
assert.ok(!modes.check({ mode: 'fill', fields: [{ answer: '1.05' }] }, ['1.5']));
assert.ok(!modes.check(decimalQ, ['1.']) && !modes.check(decimalQ, ['.3']));

console.log('engine ok');
