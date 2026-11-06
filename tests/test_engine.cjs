// Grading checks for the shared renderers in shared-static/pk/types, run against the real question banks.
// Each renderer is loaded with a stub PK that only records PK.register(); no DOM is needed for check().
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.resolve(__dirname, '..');
const renderers = {};
const PK = { register: (type, r) => (renderers[type] = r), el() {}, button() {}, svg() {}, shuffle: a => [...a], figures: {} };
for (const file of ['choice', 'blanks', 'tiles', 'expand', 'edit', 'measure']) {
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
console.log('engine ok');
