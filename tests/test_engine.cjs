// Grading checks for the shared renderers in shared-static/pk/types, run against the real question banks.
// Each renderer is loaded with a stub PK that only records PK.register(); no DOM is needed for check().
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const ROOT = path.resolve(__dirname, '..');
const renderers = {};
const PK = { register: (type, r) => (renderers[type] = r), el() {}, button() {}, svg() {}, shuffle: a => [...a], figures: {} };
for (const file of ['choice', 'blanks', 'tiles', 'edit', 'measure']) {
  vm.runInNewContext(fs.readFileSync(path.join(ROOT, 'shared-static/pk/types', file + '.js'), 'utf8'), { PK, window: {} });
}
const bank = (site, game) => JSON.parse(fs.readFileSync(path.join(ROOT, 'sites', site, 'banks', game + '.json'), 'utf8'));
const apply = (q, e) => renderers.edit.value(q, e).value;

// 修改病句: every accepted edit is graded right, and deleting either copy of a doubled word counts.
const sentence = bank('chinese', 'sentence');
let doubled = 0;
for (const q of sentence.questions) {
  for (const e of q.accepted) assert.ok(renderers.edit.check(q, apply(q, e)), `${q.id} accepted edit graded wrong`);
  const d = q.accepted.find(e => e.kind === 'delete');
  const i = d && q.tokens.findIndex(t => t.id === d.target);
  for (const j of d ? [i - 1, i + 1] : []) {
    const twin = q.tokens[j];
    if (!twin || twin.text !== q.tokens[i].text) continue;
    assert.ok(renderers.edit.check(q, apply(q, { kind: 'delete', target: twin.id })), `${q.id} deleting the other copy graded wrong`);
    doubled++;
  }
  assert.ok(!renderers.edit.check(q, q.original), `${q.id} unchanged sentence graded right`);
}
assert.ok(doubled > 0, 'no doubled-word deletions found');

// Tiles: every listed answer is right; 缩写 needs at least one deletion before it can be submitted.
for (const game of ['passive', 'rhetorical', 'expand', 'shorten']) {
  for (const q of bank('chinese', game).questions) for (const ans of q.answers) assert.ok(renderers.tiles.check(q, ans), `${game} ${q.id}`);
}
const shorten = bank('chinese', 'shorten').questions[0];
assert.ok(renderers.tiles.value(shorten, { deleted: [] }, { ui: {} }).incomplete, 'untouched 缩写 accepted');

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
for (const [site, games] of [['chinese', ['reading', 'similar-char', 'polyphone']], ['math', ['conversion', 'mental', 'units', 'directions']]]) {
  for (const game of games) {
    for (const q of bank(site, game).questions) {
      for (const o of q.options) assert.strictEqual(renderers.choice.check(q, o), String(o) === String(q.answer), `${game} ${q.id} ${o}`);
    }
  }
}
console.log('engine ok');
