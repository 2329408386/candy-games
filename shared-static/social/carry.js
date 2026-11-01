// http -> https hand-off. The site answers on both http and https; localStorage is kept per origin, so the mistake
// books and game settings saved on http are invisible on https. Links marked data-secure (and read.js's
// "切换到安全连接") go to https with this browser's saved data packed after "#carry=". The https page merges it in
// (a mistake keeps its newest copy; settings are copied only where https has none yet) and drops it from the address.
(() => {
  'use strict';
  const PATTERN = /^(chinese|math|english)-(mistakes|pk-settings)-v1$/;
  const read = key => { try { return JSON.parse(localStorage.getItem(key) || 'null'); } catch { return null; } };
  const write = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch {} };
  const encode = text => btoa(unescape(encodeURIComponent(text))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const decode = text => decodeURIComponent(escape(atob(text.replace(/-/g, '+').replace(/_/g, '/'))));

  function link(href) {
    const data = {};
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (PATTERN.test(key)) data[key] = read(key);
      }
    } catch {}
    if (!Object.keys(data).length) return href;
    const u = new URL(href, location.href);
    u.hash = 'carry=' + encode(JSON.stringify(data)) + (u.hash ? '&' + u.hash.slice(1) : '');
    return u.href;
  }

  function receive() {
    const m = /^#carry=([A-Za-z0-9_-]+)&?(.*)$/.exec(location.hash);
    if (!m) return;
    try {
      const data = JSON.parse(decode(m[1]));
      for (const [key, value] of Object.entries(data)) {
        if (!PATTERN.test(key) || !value || typeof value !== 'object') continue;
        const mine = read(key);
        if (key.includes('-mistakes-')) {
          const book = mine && typeof mine === 'object' ? mine : {};
          for (const [id, entry] of Object.entries(value)) if (!book[id] || (entry?.last || 0) > (book[id].last || 0)) book[id] = entry;
          write(key, book);
        } else if (!mine) write(key, value);
      }
    } catch {}
    history.replaceState(null, '', location.pathname + location.search + (m[2] ? '#' + m[2] : ''));
  }

  receive();
  if (location.protocol === 'http:') {
    document.addEventListener('click', e => {
      const a = e.target.closest?.('a[data-secure]');
      if (!a) return;
      const u = new URL(a.href, location.href);
      u.protocol = 'https:';
      a.href = link(u.href);
    }, true);
  }
  window.CARRY = { link };
})();
