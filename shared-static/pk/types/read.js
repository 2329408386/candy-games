// 读句子 (English): the child reads a textbook sentence aloud and the server scores the reading.
// q = {text, zh, audio (model reading), audioText}. The page records 16 kHz 16-bit mono PCM with the Web Audio API,
// posts it to PK.page.speech.url?q=<id> (core/speech.py, which asks Alibaba Cloud 一句话识别) and gets back
// {score, pass, words: [{w, ok}], heard}. The answer is the score: 80 or more counts as right (PASS, same as the
// server). There is no 提交答案 button: the scored reading is submitted by itself; 跳过 submits a zero.
// Browsers allow the microphone only on https pages, so on http the page offers to switch (carrying this browser's
// mistake book and settings along, see carry.js).
(() => {
  'use strict';
  const { el, button } = PK;
  const PASS = 80, MAX_SECONDS = 10, RATE = 16000;
  const speech = PK.page?.speech || null;

  // ---------- microphone ----------
  // One stream for the whole visit, so the browser asks for permission once; it is released when the page is hidden.
  const mic = (() => {
    let stream = null, ctx = null, source = null, node = null, chunks = null, onLevel = null;
    let listening = false;
    async function open() {
      if (stream && stream.active) return;
      if (!listening) {
        listening = true;
        addEventListener('pagehide', close);
        document.addEventListener('visibilitychange', () => document.hidden && close());
      }
      stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      source = ctx.createMediaStreamSource(stream);
      // ScriptProcessor is deprecated but runs everywhere (old iPads, WeChat), unlike AudioWorklet.
      node = ctx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = e => {
        if (!chunks) return;
        const data = e.inputBuffer.getChannelData(0);
        chunks.push(new Float32Array(data));
        let peak = 0;
        for (let i = 0; i < data.length; i += 16) peak = Math.max(peak, Math.abs(data[i]));
        onLevel?.(peak);
      };
      source.connect(node);
      node.connect(ctx.destination); // the processor only runs while connected; it outputs silence
    }
    function close() {
      chunks = null;
      try { source?.disconnect(); node?.disconnect(); } catch {}
      stream?.getTracks().forEach(t => t.stop());
      ctx?.close().catch(() => {});
      stream = ctx = source = node = null;
    }
    async function start(level) {
      await open();
      await ctx.resume();
      onLevel = level;
      chunks = [];
    }
    // Float samples at the device rate -> 16 kHz little-endian 16-bit PCM (box-filter downsampling).
    function stop() {
      const parts = chunks || [];
      chunks = null;
      onLevel = null;
      const total = parts.reduce((n, c) => n + c.length, 0), input = new Float32Array(total);
      let at = 0;
      for (const c of parts) input.set(c, at), (at += c.length);
      const ratio = (ctx?.sampleRate || RATE) / RATE, length = Math.floor(total / ratio), out = new Int16Array(length);
      for (let i = 0; i < length; i++) {
        const from = Math.floor(i * ratio), to = Math.max(from + 1, Math.floor((i + 1) * ratio));
        let sum = 0;
        for (let j = from; j < to; j++) sum += input[j];
        const v = Math.max(-1, Math.min(1, sum / (to - from)));
        out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
      }
      return out.buffer;
    }
    return { start, stop, close };
  })();

  let csrf = null;
  const token = () => (csrf ||= fetch(speech.csrf, { cache: 'no-store', credentials: 'same-origin' }).then(r => r.json()).then(d => d.csrf)
    .catch(e => { csrf = null; throw e; }));

  async function evaluate(q, pcm) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const r = await fetch(speech.url + '?q=' + encodeURIComponent(q.id), {
        method: 'POST', cache: 'no-store', credentials: 'same-origin',
        headers: { 'X-CSRF-Token': await token(), 'Content-Type': 'application/octet-stream' }, body: pcm,
      });
      const data = await r.json().catch(() => ({ error: '服务器返回异常，请稍后再试' }));
      if (r.ok) return data;
      if (data.code === 'csrf_expired' && !attempt) { csrf = null; continue; }
      throw Error(data.error || '评测失败，请再读一次');
    }
  }

  const secureUrl = () => {
    const u = new URL(location.href);
    u.protocol = 'https:';
    return window.CARRY ? window.CARRY.link(u.href) : u.href;
  };

  function sentence(q, a) {
    const box = el('p', 'read-sentence');
    box.lang = 'en';
    const words = a.words || q.text.split(' ').map(w => ({ w, ok: null }));
    words.forEach((item, i) => {
      if (i) box.append(' ');
      box.append(el('span', item.ok === null ? '' : item.ok ? 'ok' : 'miss', item.w));
    });
    return box;
  }

  PK.register('read', {
    history: false,
    submitButton: false,
    init: () => ({ score: null, words: null, heard: '' }),
    render(q, a, ctx) {
      const wrap = el('div', 'q-read');
      wrap.append(sentence(q, a), el('p', 'read-zh', q.zh));
      if (a.score !== null) {
        const said = a.heard ? '听到：' + a.heard : '没有听清楚';
        wrap.append(el('p', 'read-score ' + (a.score >= PASS ? 'pass' : 'fail'), `得分 ${a.score}`), el('p', 'read-heard', said));
        return wrap;
      }
      const row = el('div', 'read-actions');
      const listen = button('🔊 听示范', () => PK.say(q.audio), 'secondary pk-say');
      const meter = el('span', 'read-meter');
      meter.append(el('i'));
      const talk = button('🎤 开始读', () => toggle(), 'primary read-talk');
      const skip = button('跳过', () => {
        ctx.mutate(x => Object.assign(x, { score: 0, words: null, heard: '' }));
        ctx.submit();
      }, 'text-button read-skip');
      row.append(listen, talk, meter, skip);
      wrap.append(row);
      if (!speech) {
        talk.disabled = true;
        wrap.append(el('p', 'read-help', '读句子需要先激活授权或开始试玩。'));
        return wrap;
      }
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        talk.disabled = true;
        const help = el('p', 'read-help', '浏览器只允许在安全连接（https）下使用麦克风。');
        if (location.protocol === 'http:') {
          const go = el('a', 'button primary', '🔒 切换到安全连接');
          go.href = secureUrl();
          help.append(' ', go);
        }
        wrap.append(help);
        return wrap;
      }
      let phase = 'idle', timer = 0, started = 0;
      async function toggle() {
        if (ctx.locked || phase === 'busy') return;
        if (phase === 'idle') {
          try {
            PK.quiet(true);
            await mic.start(peak => meter.firstChild.style.width = Math.min(100, peak * 160) + '%');
          } catch (e) {
            PK.quiet(false);
            ctx.notice(e.name === 'NotAllowedError' ? '没有麦克风权限：请在浏览器设置里允许本站使用麦克风，再点「开始读」。' : '打不开麦克风，请检查设备后再试。');
            return;
          }
          phase = 'rec';
          started = Date.now();
          ctx.notice('');
          talk.textContent = '⏹ 读完了';
          talk.classList.add('recording');
          listen.disabled = skip.disabled = true;
          meter.classList.add('on');
          timer = setInterval(() => {
            const left = MAX_SECONDS - Math.floor((Date.now() - started) / 1000);
            if (left <= 0) toggle();
            else talk.textContent = `⏹ 读完了（${left}）`;
          }, 250);
          return;
        }
        clearInterval(timer);
        phase = 'busy';
        const pcm = mic.stop();
        PK.quiet(false);
        meter.classList.remove('on');
        talk.classList.remove('recording');
        talk.textContent = '评测中…';
        talk.disabled = true;
        try {
          const result = await evaluate(q, pcm);
          ctx.mutate(x => Object.assign(x, { score: result.score, words: result.words, heard: result.heard }));
          ctx.submit();
        } catch (e) {
          phase = 'idle';
          talk.textContent = '🎤 再读一次';
          talk.disabled = listen.disabled = skip.disabled = false;
          ctx.notice(e instanceof TypeError ? '网络连接失败，请再读一次。' : e.message);
        }
      }
      return wrap;
    },
    afterRender(q, a, ctx) {
      if (ctx.view.spoken || ctx.result) return;
      ctx.view.spoken = true;
      if (ctx.players === 1) PK.say(q.audio);
    },
    value: (q, a) => (a.score === null ? { incomplete: '先点「开始读」把句子读出来。' } : { value: { score: a.score, heard: a.heard } }),
    check: (q, value) => value.score >= PASS,
    solution: q => `${q.text}（${q.zh}）`,
    describe: q => `读句子：${q.text}（${q.zh}）`,
  });
})();
