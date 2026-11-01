// Pictures for the English games (loaded after choice.js or tiles.js on the English game pages).
//   PK.picture(key)    -> node for one picture: a word from PICTURES (emoji, drawn SVG, a colour swatch or a number of
//                         apples), or any other text, shown as is (scene emoji such as "👋").
//   PK.figures.pic     -> the row of pictures in q.pic, drawn under the question. An item may also be
//                         {item, rel: "on" | "in", at} to show where something is (Unit 6: It's on the desk.).
(() => {
  'use strict';
  const { el, svg } = PK;

  // Drawn where no emoji exists or old devices/projector PCs lack it. viewBox 0 0 64 64.
  const SVG = {
    eraser: '<rect x="8" y="22" width="48" height="22" rx="5" fill="#f3a6b8" transform="rotate(-14 32 33)"/><rect x="8" y="22" width="20" height="22" rx="5" fill="#6f95c9" transform="rotate(-14 32 33)"/>',
    'pencil box': '<rect x="6" y="26" width="52" height="24" rx="6" fill="#6f95c9"/><rect x="6" y="26" width="52" height="8" rx="4" fill="#4a6fa5"/><path d="M16 26V12l3-5 3 5v14" fill="#f0c96a"/><path d="M28 26V15l3-5 3 5v11" fill="#e07a6a"/><path d="M40 26V13l3-5 3 5v13" fill="#7fb07f"/><circle cx="32" cy="40" r="3" fill="#fff"/>',
    desk: '<rect x="4" y="18" width="56" height="8" rx="2" fill="#b98a5a"/><rect x="8" y="26" width="5" height="30" fill="#9a6f45"/><rect x="51" y="26" width="5" height="30" fill="#9a6f45"/><rect x="30" y="26" width="21" height="14" fill="#c99a6a"/><rect x="38" y="31" width="6" height="3" rx="1.5" fill="#6b4a2c"/>',
    chair: '<rect x="18" y="6" width="28" height="26" rx="4" fill="#e07a6a"/><rect x="14" y="32" width="36" height="8" rx="3" fill="#c9604f"/><rect x="17" y="40" width="5" height="18" fill="#8a5a3a"/><rect x="42" y="40" width="5" height="18" fill="#8a5a3a"/>',
    window: '<rect x="10" y="8" width="44" height="48" rx="3" fill="#cfe6f7" stroke="#6b8bb5" stroke-width="4"/><path d="M32 8v48M10 32h44" stroke="#6b8bb5" stroke-width="4"/><path d="M15 26l8-12M38 26l8-12" stroke="#fff" stroke-width="3" stroke-linecap="round"/>',
    room: '<path d="M6 10h52v44H6z" fill="#f6ecd9"/><path d="M6 44h52v10H6z" fill="#c99a6a"/><rect x="36" y="16" width="14" height="14" fill="#cfe6f7" stroke="#6b8bb5" stroke-width="2"/><rect x="10" y="34" width="22" height="10" rx="2" fill="#6f95c9"/><rect x="10" y="30" width="6" height="8" rx="2" fill="#fff"/><path d="M6 10h52v44H6z" fill="none" stroke="#9a6f45" stroke-width="3"/>',
  };
  const EMOJI = {
    pen: '🖊️', pencil: '✏️', book: '📕', ruler: '📏', bag: '🎒',
    mum: '👩', dad: '👨', grandma: '👵', grandpa: '👴', sister: '👧', brother: '👦',
    home: '🏠', bed: '🛏️', door: '🚪',
    hello: '👋', hi: '👋', goodbye: '👋', bye: '👋', friend: '👫', friends: '👫', boy: '👦', girl: '👧', he: '👦', she: '👧',
    family: '👨‍👩‍👧‍👦', cat: '🐱\uFE0F', dog: '🐶', milk: '🥛', apple: '🍎',
  };
  const COLOURS = {
    red: '#e2463f', yellow: '#f6cf2f', green: '#4caf50', blue: '#3b7ddd', black: '#222222', white: '#ffffff', orange: '#f5922f', brown: '#8b5a2b',
  };
  const NUMBERS = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };

  function picture(key) {
    const k = String(key);
    if (SVG[k]) return svg(`<svg viewBox="0 0 64 64" aria-hidden="true">${SVG[k]}</svg>`, 'pic pic-svg');
    if (EMOJI[k]) return el('span', 'pic pic-emoji', EMOJI[k]);
    if (COLOURS[k]) {
      const node = svg(`<svg viewBox="0 0 64 64" aria-hidden="true"><path d="M32 6c12 0 26 8 26 24S46 58 30 58 6 48 6 32 20 6 32 6z" fill="${COLOURS[k]}" stroke="#7a8590" stroke-width="${k === 'white' ? 2.5 : 0}"/></svg>`, 'pic pic-colour');
      return node;
    }
    if (NUMBERS[k]) {
      const n = NUMBERS[k], box = el('span', 'pic pic-count' + (n > 6 ? ' many' : ''));
      for (let i = 0; i < n; i++) box.append(el('i', '', '🍎'));
      return box;
    }
    return el('span', 'pic pic-emoji', k);
  }

  function placed(item) {
    const box = el('span', 'pic-place ' + item.rel);
    const thing = picture(item.item), at = picture(item.at);
    thing.classList.add('pic-item');
    at.classList.add('pic-at');
    box.append(at, thing);
    return box;
  }

  PK.picture = picture;
  PK.pictureKeys = new Set([...Object.keys(SVG), ...Object.keys(EMOJI), ...Object.keys(COLOURS), ...Object.keys(NUMBERS)]);
  PK.figures.pic = q => {
    const row = el('div', 'pic-row' + (q.pic.length > 4 ? ' many' : ''));
    row.setAttribute('role', 'img');
    row.setAttribute('aria-label', q.picText || '图片');
    for (const item of q.pic) row.append(typeof item === 'object' ? placed(item) : picture(item));
    return row;
  };
})();
