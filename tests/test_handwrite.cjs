// 字母奇妙屋 recogniser (shared-static/pk/types/handwrite.js) against distorted copies of its own templates:
// shifted, scaled, squashed, slanted, rotated, jittered, strokes reordered and reversed — roughly how a child's
// letter differs from print. Each letter must be read as itself, and a letter must not pass as another one.
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const PK = { register() {}, el() {}, button() {} };
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../shared-static/pk/types/handwrite.js'), 'utf8'), { PK, window: {} });
const { read, rank, templates } = PK.handwriting;

let seed = 7;
const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
const between = (a, b) => a + (b - a) * rand();
function densify(stroke, step = 0.06) {
  const out = [stroke[0]];
  for (let i = 1; i < stroke.length; i++) {
    const [x0, y0] = stroke[i - 1], [x1, y1] = stroke[i], n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / step));
    for (let k = 1; k <= n; k++) out.push([x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n]);
  }
  return out;
}
function distort(strokes) {
  const s = between(0.85, 1.12), aspect = between(0.85, 1.18), rot = between(-0.1, 0.1), shear = between(-0.15, 0.15);
  const dx = between(-1, 1), dy = between(-0.15, 0.15), cy = 1;
  let out = strokes.map(st => densify(st).map(([x, y]) => {
    let u = x * aspect, v = y - cy;
    u += shear * v;
    [u, v] = [u * Math.cos(rot) - v * Math.sin(rot), u * Math.sin(rot) + v * Math.cos(rot)];
    return [u * s + dx + between(-0.03, 0.03), v * s + cy + dy + between(-0.03, 0.03)];
  }));
  out = out.map(st => (rand() < 0.4 ? st.reverse() : st));
  if (rand() < 0.5) out.reverse();
  return out;
}

const letters = Object.keys(templates), SAMPLES = +(process.env.SAMPLES || 8), wrongs = [];
let total = 0, right = 0;
for (const letter of letters) {
  let ok = 0;
  for (let i = 0; i < SAMPLES; i++) {
    const drawn = distort(templates[letter]);
    const ranked = rank(drawn);
    if (read(drawn, letter, ranked) === letter) ok++;
    // the same drawing must not pass for a different letter
    for (const other of letters) if (other !== letter && !['Il', 'lI'].includes(letter + other) && read(drawn, other, ranked) === other) wrongs.push(letter + '→' + other);
  }
  total += SAMPLES, right += ok;
  if (process.env.VERBOSE || ok < SAMPLES * 0.9) console.log(letter, ok + '/' + SAMPLES);
  assert.ok(ok >= SAMPLES * 0.9, `${letter} read as itself only ${ok}/${SAMPLES}`);
}
const rate = wrongs.length / (total * (letters.length - 1));
console.log(`letters ${(100 * right / total).toFixed(1)}% read right; wrong letter accepted ${(100 * rate).toFixed(2)}%`,
  process.env.VERBOSE ? Object.entries(wrongs.reduce((m, w) => ((m[w] = (m[w] || 0) + 1), m), {})).sort((a, b) => b[1] - a[1]).slice(0, 25) : '');
assert.ok(right / total >= 0.97, 'overall');
assert.ok(rate <= 0.002, 'wrong letters accepted too often');
console.log('handwrite ok');
