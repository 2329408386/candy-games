/* 语文老师工作台: small helpers shared by every page (forms, photo uploads, tabs, the account menu), and the
 * teacher's review editor (review.html), whose red-pen preview is drawn by redpen.js. */
(function () {
  'use strict';
  const WB = (window.WB = {});
  let token = null;

  WB.csrf = async function () {
    if (!token) {
      const r = await fetch(window.WB_CSRF, { cache: 'no-store' });
      token = (await r.json()).csrf;
    }
    return token;
  };

  WB.post = async function (url, data) {
    const send = async () =>
      fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': await WB.csrf() },
        body: JSON.stringify(data || {}),
      });
    let r = await send();
    if (r.status === 400) {
      const body = await r.clone().json().catch(() => ({}));
      if (body.code === 'csrf_expired') {
        token = null;
        r = await send();
      }
    }
    const body = await r.json().catch(() => ({}));
    if (!r.ok || body.error) throw new Error(body.error || '请求失败，请稍后重试');
    return body;
  };

  /* Posts a FormData or a Blob (uploads) with the CSRF token; same answers and errors as WB.post. */
  WB.upload = async function (url, body) {
    const send = async () => fetch(url, { method: 'POST', headers: { 'X-CSRF-Token': await WB.csrf() }, body });
    let r = await send();
    if (r.status === 400) {
      const answer = await r.clone().json().catch(() => ({}));
      if (answer.code === 'csrf_expired') {
        token = null;
        r = await send();
      }
    }
    if (r.status === 413) throw new Error('照片太大了，请少传几张或重新拍');
    const answer = await r.json().catch(() => ({}));
    if (!r.ok || answer.error) throw new Error(answer.error || '上传失败，请检查网络后重试');
    return answer;
  };

  /* A photo, upright and at most `side` px, as a JPEG blob; `square` crops the middle (avatars). */
  WB.shrink = async function (file, side, quality, square) {
    let source;
    try {
      source = await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch (e) {
      source = await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error('这张照片打不开，请换一张'));
        img.src = URL.createObjectURL(file);
      });
    }
    const w = source.width, h = source.height;
    const crop = square ? Math.min(w, h) : 0;
    const sw = square ? crop : w, sh = square ? crop : h;
    const scale = Math.min(1, side / Math.max(sw, sh));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(sw * scale);
    canvas.height = Math.round(sh * scale);
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(source, square ? (w - crop) / 2 : 0, square ? (h - crop) / 2 : 0, sw, sh, 0, 0, canvas.width, canvas.height);
    return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('照片处理失败，请换一张'))), 'image/jpeg', quality));
  };

  /* <div class="photo-picker" data-max="3">: pages of an essay, in order, shrunk in the browser before uploading. */
  WB.photoPicker = function (root) {
    if (!root) return { files: () => [] };
    const max = Number(root.dataset.max || 3);
    const list = root.querySelector('.photo-list');
    const input = root.querySelector('input[type=file]');
    const add = root.querySelector('.photo-add');
    const items = [];
    const draw = () => {
      list.innerHTML = items
        .map((it, i) => '<figure class="photo-item"><img src="' + it.url + '" alt="第 ' + (i + 1) + ' 页"><figcaption>第 ' + (i + 1) + ' 页</figcaption><button type="button" class="photo-remove" data-remove="' + i + '" aria-label="删掉第 ' + (i + 1) + ' 页">×</button></figure>')
        .join('');
      add.hidden = items.length >= max;
    };
    input.addEventListener('change', async () => {
      const files = [...input.files].slice(0, max - items.length);
      input.value = '';
      add.classList.add('busy');
      for (const f of files) {
        try {
          const blob = await WB.shrink(f, 2000, 0.85);
          items.push({ blob, url: URL.createObjectURL(blob) });
        } catch (err) {
          alert(err.message);
        }
      }
      add.classList.remove('busy');
      draw();
    });
    list.addEventListener('click', (e) => {
      const b = e.target.closest('[data-remove]');
      if (!b) return;
      URL.revokeObjectURL(items[Number(b.dataset.remove)].url);
      items.splice(Number(b.dataset.remove), 1);
      draw();
    });
    draw();
    return { files: () => items.map((it) => it.blob) };
  };

  /* .tabs buttons [data-tab] show the matching [data-pane] inside the same parent. */
  WB.tabs = function (root) {
    const tabs = root.querySelectorAll(':scope > .tabs .tab');
    tabs.forEach((t) =>
      t.addEventListener('click', () => {
        tabs.forEach((x) => {
          x.classList.toggle('active', x === t);
          x.setAttribute('aria-selected', x === t);
        });
        root.querySelectorAll(':scope > [data-pane]').forEach((p) => (p.hidden = p.dataset.pane !== t.dataset.tab));
        root.dispatchEvent(new CustomEvent('tab', { detail: t.dataset.tab }));
      })
    );
  };
  document.querySelectorAll('.tabs').forEach((t) => WB.tabs(t.parentElement));

  /* An essay form: photos when there are any (and the photo tab is showing), otherwise the typed text. */
  WB.submitEssay = function (selector, url, picker, done) {
    const form = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const button = form.querySelector('button:not([type=button])');
      const error = form.querySelector('.wb-error');
      const pane = form.querySelector('[data-pane=photo]');
      const photos = picker.files();
      const usePhotos = pane ? !pane.hidden : photos.length > 0;
      error.textContent = '';
      const fields = formData(form);
      if (usePhotos && !photos.length) {
        error.textContent = '请先拍照：作文有几页就拍几张';
        return;
      }
      if (!usePhotos && !(fields.text || '').trim()) {
        error.textContent = '请先写好作文';
        return;
      }
      const label = button.textContent;
      button.disabled = true;
      button.textContent = usePhotos ? '正在上传照片…' : '正在提交…';
      try {
        let answer;
        if (usePhotos) {
          const body = new FormData();
          for (const [k, v] of Object.entries(fields)) if (k !== 'text') body.append(k, v);
          photos.forEach((b, i) => body.append('photos', b, 'page' + (i + 1) + '.jpg'));
          answer = await WB.upload(url, body);
        } else {
          answer = await WB.post(url, fields);
        }
        done(answer);
      } catch (err) {
        error.textContent = err.message;
      } finally {
        button.disabled = false;
        button.textContent = label;
      }
    });
  };

  /* The account menu in the header closes when tapping elsewhere. */
  document.addEventListener('click', (e) => {
    document.querySelectorAll('details.me-menu[open]').forEach((d) => {
      if (!d.contains(e.target)) d.open = false;
    });
  });

  function formData(form) {
    const data = {};
    for (const el of form.elements) {
      if (!el.name || el.type === 'file') continue;
      if (el.type === 'checkbox') data[el.name] = el.checked;
      else data[el.name] = el.value;
    }
    return data;
  }

  /* Posts a form as JSON; shows the error in its .wb-error; calls done(response) on success. */
  WB.form = function (selector, url, done) {
    const form = typeof selector === 'string' ? document.querySelector(selector) : selector;
    if (!form) return;
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const button = form.querySelector('button:not([type=button])');
      const error = form.querySelector('.wb-error');
      if (error) error.textContent = '';
      if (button) button.disabled = true;
      try {
        done(await WB.post(url || form.dataset.url, formData(form)), form);
      } catch (err) {
        if (error) error.textContent = err.message;
        else alert(err.message);
      } finally {
        if (button) button.disabled = false;
      }
    });
  };

  /* <button data-post="url" data-confirm="…" data-body='{"a":1}'> posts, then follows the answer's url or reloads. */
  document.addEventListener('click', async (e) => {
    const el = e.target.closest('[data-post]');
    if (!el) return;
    e.preventDefault();
    if (el.dataset.confirm && !confirm(el.dataset.confirm)) return;
    el.disabled = true;
    try {
      const body = await WB.post(el.dataset.post, el.dataset.body ? JSON.parse(el.dataset.body) : {});
      if (el.dataset.show && body[el.dataset.show] !== undefined) alert(el.dataset.showLabel + body[el.dataset.show]);
      if (body.url) location.href = body.url;
      else location.reload();
    } catch (err) {
      alert(err.message);
      el.disabled = false;
    }
  });

  /* Polls a submission's state every 3 s until it leaves the queue, then calls done(state). */
  WB.poll = function (url, onState, done) {
    let stopped = false;
    const tick = async () => {
      if (stopped) return;
      try {
        const r = await fetch(url, { cache: 'no-store' });
        const s = await r.json();
        onState && onState(s);
        if (s.status !== 'queued' && s.status !== 'grading') {
          done(s);
          return;
        }
      } catch (err) {
        /* keep trying */
      }
      setTimeout(tick, 3000);
    };
    tick();
    return () => (stopped = true);
  };

  document.querySelectorAll('[data-poll]').forEach((el) => {
    WB.poll(
      el.dataset.poll,
      (s) => {
        const label = el.querySelector('[data-label]');
        if (label) label.textContent = s.label + (s.hold ? '：' + s.hold : '');
      },
      () => location.reload()
    );
  });

  WB.esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

  /* The essay text as paragraphs, with marks as <mark data-i> spans; each text run carries data-o (its offset). */
  WB.annotate = function (text, marks, active) {
    const order = marks.map((m, i) => ({ ...m, i })).sort((a, b) => a.start - b.start || b.end - a.end);
    let html = '', pos = 0;
    const run = (from, to) => {
      // Split on newlines so paragraphs stay paragraphs; offsets stay exact.
      let out = '', start = from;
      for (let k = from; k <= to; k++) {
        if (k === to || text[k] === '\n') {
          if (k > start) out += '<span data-o="' + start + '">' + WB.esc(text.slice(start, k)) + '</span>';
          if (k < to) out += '<br data-o="' + k + '">';
          start = k + 1;
        }
      }
      return out;
    };
    for (const m of order) {
      if (m.start < pos) continue; // overlapping marks are listed but not drawn twice
      html += run(pos, m.start);
      html += '<mark class="mk mk-' + typeClass(m.type) + (m.i === active ? ' active' : '') + '" data-i="' + m.i + '">' + run(m.start, m.end) + '</mark>';
      pos = m.end;
    }
    html += run(pos, text.length);
    return html;
  };

  function typeClass(type) {
    return { 错别字: 'typo', 用词不当: 'word', 病句: 'sentence', 标点: 'punct', 好词好句: 'good', 修改建议: 'tip' }[type] || 'tip';
  }
  WB.typeClass = typeClass;

  /* Offset in the essay text of a point inside an annotated container (for turning a selection into a mark). */
  WB.offsetOf = function (container, node, offset) {
    let el = node.nodeType === 3 ? node.parentElement : node;
    if (node.nodeType !== 3) {
      const child = node.childNodes[offset] || node.childNodes[node.childNodes.length - 1];
      if (!child) return null;
      el = child.nodeType === 3 ? child.parentElement : child;
      offset = 0;
    }
    while (el && el !== container && el.dataset.o === undefined) el = el.parentElement;
    if (!el || el === container) return null;
    return Number(el.dataset.o) + offset;
  };

  // redpen.js loads after this file: the editor starts once the page is parsed.
  if (window.REVIEW) document.addEventListener('DOMContentLoaded', () => reviewEditor(window.REVIEW));

  function levelOf(total) {
    // Out of 25: 优 23–25, 良 19–22, 及格 15–18 (grading.level_of).
    return total >= 23 ? '优' : total >= 19 ? '良' : total >= 15 ? '及格' : '待提高';
  }

  function reviewEditor(page) {
    const root = document.getElementById('review-editor');
    if (!root || !page.review) return;
    const state = JSON.parse(JSON.stringify(page.review));
    state.keep_example = !!state.example;
    let active = null, dirty = false;
    const essay = root.querySelector('.essay-text');
    const list = root.querySelector('.mark-list');
    const dims = root.querySelector('.dims-edit');
    const status = root.querySelector('.save-status');

    const preview = document.getElementById('redpen-preview');
    let previewTimer = null;
    function drawPreview() {
      if (!preview || !page.redpen || !window.RedPen) return;
      const total = Object.values(state.dims).reduce((a, d) => a + d.score, 0);
      RedPen.render(preview, {
        ...page.redpen,
        marks: state.marks.map((m) => ({ type: m.type, start: m.start, end: m.end, fix: m.fix || '', note: m.note || '' })),
        total,
        level: levelOf(total),
        summary: summary.value.trim(),
        praise: lines(praise),
        advice: lines(advice),
      });
    }

    function setDirty() {
      dirty = true;
      status.textContent = '有修改，还没保存';
      clearTimeout(previewTimer);
      previewTimer = setTimeout(drawPreview, 400);
    }

    function drawEssay() {
      essay.innerHTML = WB.annotate(page.text, state.marks, active);
    }

    function drawTotal() {
      const total = Object.values(state.dims).reduce((a, d) => a + d.score, 0);
      root.querySelector('.total-score').textContent = total;
      root.querySelector('.total-level').textContent = levelOf(total);
    }

    function drawDims() {
      // dimOrder keeps 内容/结构/语言/规范 in order (JSON objects from Flask come with sorted keys).
      dims.innerHTML = page.dimOrder
        .map(
          (key) =>
            '<label class="dim-row"><span>' + page.dimNames[key] + '</span><input type="number" min="0" max="' + page.max[key] + '" value="' + state.dims[key].score +
            '" data-dim="' + key + '"><small>/ ' + page.max[key] + '</small><em>' + WB.esc(state.dims[key].reason || '') + '</em></label>'
        )
        .join('');
      drawTotal();
    }

    function drawMarks() {
      const options = (t) => page.types.map((x) => '<option' + (x === t ? ' selected' : '') + '>' + x + '</option>').join('');
      list.innerHTML = state.marks.length
        ? state.marks
            .map(
              (m, i) =>
                '<li class="mark-item' + (i === active ? ' active' : '') + '" data-i="' + i + '"><div class="mark-head"><span class="chip mk-' + typeClass(m.type) + '">' +
                '<select data-field="type">' + options(m.type) + '</select></span><q>' + WB.esc(page.text.slice(m.start, m.end)) +
                '</q><button type="button" class="link-button" data-remove>删除</button></div>' +
                '<label>改为<input data-field="fix" maxlength="120" value="' + WB.esc(m.fix || '') + '"></label>' +
                '<label>说明<input data-field="note" maxlength="120" value="' + WB.esc(m.note || '') + '"></label></li>'
            )
            .join('')
        : '<li class="hint">没有标注。在左边作文里选中文字，可以添加标注。</li>';
      drawRejected();
    }

    function drawRejected() {
      const box = root.querySelector('.rejected');
      if (!box) return;
      const items = state.rejected || [];
      box.hidden = !items.length;
      box.querySelector('ul').innerHTML = items
        .map(
          (m, i) =>
            '<li><span class="chip mk-' + typeClass(m.type) + '">' + m.type + '</span><q>' + WB.esc(m.quote) + '</q> → ' + WB.esc(m.fix) +
            '<br><small>复核意见：' + WB.esc(m.reason || '不成立') + '</small> <button type="button" class="link-button" data-restore="' + i + '">恢复这条</button></li>'
        )
        .join('');
    }

    function drawAll() {
      drawEssay();
      drawMarks();
    }

    function select(i) {
      active = i;
      drawAll();
      const item = list.querySelector('[data-i="' + i + '"]');
      if (item) item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }

    essay.addEventListener('click', (e) => {
      const mark = e.target.closest('mark[data-i]');
      if (mark && window.getSelection().isCollapsed) select(Number(mark.dataset.i));
    });

    const addButton = root.querySelector('.add-mark');
    document.addEventListener('selectionchange', () => {
      const sel = window.getSelection();
      addButton.disabled = !(sel.rangeCount && !sel.isCollapsed && essay.contains(sel.anchorNode) && essay.contains(sel.focusNode));
    });
    addButton.addEventListener('mousedown', (e) => e.preventDefault()); // keep the selection
    addButton.addEventListener('click', () => {
      const sel = window.getSelection();
      if (!sel.rangeCount) return;
      const range = sel.getRangeAt(0);
      let start = WB.offsetOf(essay, range.startContainer, range.startOffset);
      let end = WB.offsetOf(essay, range.endContainer, range.endOffset);
      if (start === null || end === null || end <= start) return;
      while (start < end && /\s/.test(page.text[start])) start++;
      while (end > start && /\s/.test(page.text[end - 1])) end--;
      if (end <= start) return;
      state.marks.push({ type: '修改建议', start, end, quote: page.text.slice(start, end), fix: '', note: '' });
      state.marks.sort((a, b) => a.start - b.start);
      sel.removeAllRanges();
      setDirty();
      select(state.marks.findIndex((m) => m.start === start && m.end === end));
    });

    list.addEventListener('click', (e) => {
      const item = e.target.closest('.mark-item');
      if (!item) return;
      const i = Number(item.dataset.i);
      if (e.target.closest('[data-remove]')) {
        state.marks.splice(i, 1);
        active = null;
        setDirty();
        drawAll();
      } else if (active !== i && !e.target.closest('input,select')) {
        select(i);
      }
    });
    list.addEventListener('input', (e) => {
      const item = e.target.closest('.mark-item');
      const field = e.target.dataset.field;
      if (!item || !field) return;
      state.marks[Number(item.dataset.i)][field] = e.target.value;
      setDirty();
      if (field === 'type') drawEssay();
    });
    list.addEventListener('focusin', (e) => {
      const item = e.target.closest('.mark-item');
      if (item && Number(item.dataset.i) !== active) {
        active = Number(item.dataset.i);
        drawEssay();
        list.querySelectorAll('.mark-item').forEach((li) => li.classList.toggle('active', li === item));
      }
    });

    const rejected = root.querySelector('.rejected');
    if (rejected)
      rejected.addEventListener('click', (e) => {
        const b = e.target.closest('[data-restore]');
        if (!b) return;
        const m = state.rejected.splice(Number(b.dataset.restore), 1)[0];
        delete m.reason;
        state.marks.push(m);
        state.marks.sort((a, b) => a.start - b.start);
        setDirty();
        drawAll();
      });

    dims.addEventListener('input', (e) => {
      const key = e.target.dataset.dim;
      if (!key) return;
      const v = Math.round(Number(e.target.value));
      state.dims[key].score = Number.isFinite(v) ? Math.max(0, Math.min(page.max[key], v)) : 0;
      setDirty();
      drawTotal();
    });

    const lines = (el) => el.value.split('\n').map((s) => s.trim()).filter(Boolean);
    const summary = root.querySelector('[name=summary]');
    const praise = root.querySelector('[name=praise]');
    const advice = root.querySelector('[name=advice]');
    const keepExample = root.querySelector('[name=keep_example]');
    summary.value = state.summary || '';
    praise.value = (state.praise || []).join('\n');
    advice.value = (state.advice || []).join('\n');
    for (const el of [summary, praise, advice]) el.addEventListener('input', setDirty);
    if (keepExample) {
      keepExample.checked = state.keep_example;
      keepExample.addEventListener('change', setDirty);
    }

    async function save(publish) {
      const review = {
        dims: Object.fromEntries(Object.entries(state.dims).map(([k, d]) => [k, { score: d.score }])),
        marks: state.marks.map((m) => ({ type: m.type, start: m.start, end: m.end, fix: m.fix || '', note: m.note || '' })),
        summary: summary.value,
        praise: lines(praise),
        advice: lines(advice),
        keep_example: keepExample ? keepExample.checked : false,
      };
      status.textContent = '正在保存…';
      try {
        await WB.post(page.urls.save, { review, publish });
        dirty = false;
        status.textContent = publish ? '已发布' : '已保存';
        return true;
      } catch (err) {
        status.textContent = '';
        alert(err.message);
        return false;
      }
    }

    root.querySelector('[data-save]').addEventListener('click', () => save(false));
    const publishButton = root.querySelector('[data-publish]');
    if (publishButton)
      publishButton.addEventListener('click', async () => {
        if (await save(true)) location.href = page.urls.next;
      });
    addEventListener('beforeunload', (e) => {
      if (dirty) e.preventDefault();
    });

    drawDims();
    drawAll();
    drawPreview();
  }
})();
