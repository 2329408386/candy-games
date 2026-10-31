// Shared engine for every PK game on both sites.
//
// A game page is a shell holding <script id="game-config"> plus this file and one question-type renderer from
// ./types/. The engine loads the licensed bank (../games/<id>/bank.json), draws questions, runs the round
// (one player or two, one shared deadline), scores answers, records mistakes and shows the results.
// Renderers register with PK.register(type, renderer); see the contract above `register` below.
// The server injects window.PK_PAGE = {grade, trial}: trial is null for licensed browsers, or {start, csrf} URLs when
// this browser may play one free round of the game (starting the round uses it up).
(() => {
  'use strict';
  const cfg = JSON.parse(document.getElementById('game-config').textContent);
  const PAGE = window.PK_PAGE || {};
  const TRIAL = PAGE.trial || null;
  const QR = 'static/shared/contact/wechat-qr.0736f95e.jpeg';
  const SUBJECT = { chinese: '语文', math: '数学' }[cfg.site];
  const PER_PLAYER = 20;
  const NAMES = ['玩家一', '玩家二'];
  const base = new URL('../', document.baseURI); // site root for /games/<id>, /math/games/<id> and legacy /<name>.html
  const url = path => new URL(path, base).href;

  // ---------- small DOM helpers (also used by the renderers) ----------
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function button(text, onClick, cls = 'card') {
    const b = el('button', cls, text);
    b.type = 'button';
    b.onclick = onClick;
    return b;
  }
  function shuffle(items) {
    const out = [...items];
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
  }
  function svg(html, cls) {
    const wrap = el('div', cls);
    wrap.innerHTML = html;
    return wrap;
  }
  const clone = value => JSON.parse(JSON.stringify(value));

  // ---------- browser storage (may be unavailable: private mode, blocked site data) ----------
  function readStore(key, fallback) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || 'null');
      return value && typeof value === 'object' && !Array.isArray(value) ? value : fallback;
    } catch {
      return fallback;
    }
  }
  function writeStore(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {}
  }

  // ---------- settings ----------
  const SETTINGS_KEY = cfg.site + '-pk-settings-v1';
  const validSeconds = n => Number.isInteger(n) && n >= 30 && n <= 1800;
  let settings = readStore(SETTINGS_KEY, {});
  let musicOn = settings.music !== false, fxOn = settings.fx !== false, voiceOn = settings.voice !== false;
  let presentOn = settings.present === true;
  let duration = validSeconds(settings.durations?.[cfg.id]) ? settings.durations[cfg.id] : cfg.defaultSeconds;
  function saveSettings() {
    settings = readStore(SETTINGS_KEY, {});
    settings.music = musicOn;
    settings.fx = fxOn;
    settings.voice = voiceOn;
    settings.present = presentOn;
    if (!settings.durations || typeof settings.durations !== 'object' || Array.isArray(settings.durations)) settings.durations = {};
    settings.durations[cfg.id] = duration;
    writeStore(SETTINGS_KEY, settings);
  }

  // ---------- mistake book: { "<game>/<id>": {game, id, text, answer, count, last} } ----------
  const MISTAKES_KEY = cfg.site + '-mistakes-v1';
  const MISTAKE_LIMIT = 300;
  const mistakes = {
    all: () => readStore(MISTAKES_KEY, {}),
    // Only ids still in the bank count, so removed or replaced questions drop out of 错题重练.
    ids: () => Object.values(mistakes.all()).filter(m => m.game === cfg.id && (!bank || bank.questions.some(q => q.id === m.id))).map(m => m.id),
    add(q, renderer) {
      const book = mistakes.all();
      const key = cfg.id + '/' + q.id;
      const old = book[key];
      book[key] = { game: cfg.id, id: q.id, text: renderer.describe(q).slice(0, 160), answer: renderer.solution(q).slice(0, 160),
        count: (old?.count || 0) + 1, last: Date.now() };
      const keys = Object.keys(book);
      if (keys.length > MISTAKE_LIMIT) {
        keys.sort((a, b) => book[a].last - book[b].last).slice(0, keys.length - MISTAKE_LIMIT).forEach(k => delete book[k]);
      }
      writeStore(MISTAKES_KEY, book);
    },
    remove(q) {
      const book = mistakes.all();
      delete book[cfg.id + '/' + q.id];
      writeStore(MISTAKES_KEY, book);
    },
  };

  // ---------- audio (music ducks under feedback and cheers; nothing plays until the first click) ----------
  const AUDIO_ROOT = url('static/game-audio-v3/');
  const AUDIO = Object.freeze({ background: 'GameSceneBGM.901e1914.mp3', click: 'effect-6.f04fbe20.mp3', success: 'effect-3.ab6b19f8.mp3',
    'success-combo': 'effect-5.f2efcf54.mp3', 'success-streak': 'effect-8.c1e4c173.mp3', failure: 'effect-1.7f41f014.mp3',
    cheer: 'effect-2.fc93a259.mp3' });
  const audio = (() => {
    const bg = new Audio();
    bg.loop = true;
    bg.preload = 'none';
    bg.volume = 0.45;
    const effects = {}, playing = new Map();
    let voice = null, volumeFrame = 0, unlocked = false, lastClick = 0, lastVoice = -Infinity;
    function duck() {
      const target = voice && !voice.paused ? 0.06 : playing.size ? 0.12 : 0.45;
      cancelAnimationFrame(volumeFrame);
      const step = () => {
        bg.volume += (target - bg.volume) * 0.25;
        if (Math.abs(target - bg.volume) > 0.002) volumeFrame = requestAnimationFrame(step);
        else bg.volume = target;
      };
      volumeFrame = requestAnimationFrame(step);
    }
    function halt() {
      for (const a of playing.values()) a.pause();
      playing.clear();
      effects.click?.pause();
      voice?.pause();
      voice = null;
      duck();
    }
    function play(name, player = 0) {
      if (!unlocked || !fxOn || document.hidden) return;
      try {
        const channel = name === 'click' ? 'click' : 'feedback-' + player;
        playing.get(channel)?.pause();
        playing.delete(channel);
        const poolKey = name === 'click' ? 'click' : name + ':' + player;
        const a = effects[poolKey] || (effects[poolKey] = new Audio(AUDIO_ROOT + AUDIO[name]));
        a.currentTime = 0;
        a.volume = name === 'click' ? 0.7 : 0.55;
        if (name !== 'click') {
          playing.set(channel, a);
          a.onended = a.onerror = () => {
            if (playing.get(channel) === a) playing.delete(channel);
            duck();
          };
        }
        a.play().then(duck).catch(() => {
          if (playing.get(channel) === a) playing.delete(channel);
          duck();
        });
        duck();
      } catch {}
    }
    function cheer() {
      if (!unlocked || !fxOn || !voiceOn || !round.running || document.hidden) return;
      if (performance.now() - lastVoice < 3000 || (voice && !voice.paused)) return;
      lastVoice = performance.now();
      const current = (voice = new Audio(AUDIO_ROOT + AUDIO.cheer));
      current.volume = 0.5;
      current.onended = current.onerror = () => {
        if (voice === current) voice = null;
        duck();
      };
      current.play().then(duck).catch(() => {
        if (voice === current) voice = null;
        duck();
      });
      duck();
    }
    function resume() {
      if (unlocked && musicOn && round.running && !document.hidden) {
        if (!bg.src) bg.src = AUDIO_ROOT + AUDIO.background;
        bg.play().catch(() => {});
      } else bg.pause();
    }
    document.addEventListener('click', e => {
      if (!e.target.closest('button,a,input[type=checkbox],select')) return;
      unlocked = true;
      const now = performance.now();
      if (now - lastClick > 65) {
        lastClick = now;
        play('click');
      }
      resume();
    }, true);
    return {
      result(correct, player, combo) {
        play(correct ? (combo >= 5 ? 'success-streak' : combo >= 2 ? 'success-combo' : 'success') : 'failure', player);
        if (correct && combo >= 5) cheer();
      },
      resume,
      stop() {
        bg.pause();
        halt();
      },
      settingsChanged() {
        if (!fxOn) halt();
        else if (!voiceOn && voice) {
          voice.pause();
          voice = null;
          duck();
        }
        resume();
      },
      unlock() {
        unlocked = true;
      },
      resetCheer() {
        lastVoice = -Infinity;
      },
    };
  })();

  // ---------- renderers ----------
  // PK.register(type, renderer). A renderer works on one question q and the player's answer state a:
  //   init(q, ctx)            -> initial answer state (plain JSON; undo snapshots it). May fill ctx.view (kept per question).
  //   render(q, a, ctx)       -> Node for the question area. ctx.change(fn) mutates a with undo + re-render,
  //                              ctx.mutate(fn) mutates without re-render, ctx.notice(text) shows a hint,
  //                              ctx.locked is true after submitting, ctx.result = {correct, value} once answered.
  //   value(q, a, ctx)        -> {value} or {incomplete: '提示'}
  //   check(q, value, ctx)    -> boolean
  //   solution(q)             -> correct answer as text;  describe(q) -> one-line question text (mistake book)
  //   history                 -> false hides 撤回/清空;  afterRender(q, a, ctx, node) -> optional, runs after insertion
  const renderers = {};
  let bank = null, renderer = null;

  // ---------- round state ----------
  const round = { running: false, ended: false, deadline: 0, timer: 0, mode: 'pk', players: [] };
  const modeLabel = { pk: '双人PK', solo: '单人练习', mistakes: '错题重练' };

  function drawQuestions(count) {
    const questions = bank.questions;
    if (round.mode === 'mistakes') {
      const wanted = new Set(mistakes.ids());
      return [shuffle(questions.filter(q => wanted.has(q.id))).slice(0, PER_PLAYER)];
    }
    const draw = bank.draw;
    if (draw.count) {
      const pool = shuffle(questions);
      return Array.from({ length: count }, (_, p) => pool.slice(p * PER_PLAYER, (p + 1) * PER_PLAYER));
    }
    // Quotas per category keep both players' rounds alike; the same pool is split, so nobody repeats a question.
    const plans = draw.alternatives || [draw.counts];
    const plan = plans[Math.floor(Math.random() * plans.length)];
    const rounds = Array.from({ length: count }, () => []);
    for (const [key, take] of Object.entries(plan)) {
      const pool = shuffle(questions.filter(q => draw.by.map(field => String(q[field])).join('/') === key));
      if (pool.length < take * count) throw Error('题库分类题量不足，请刷新页面。');
      rounds.forEach((r, p) => r.push(...pool.slice(p * take, (p + 1) * take)));
    }
    return rounds.map(shuffle);
  }

  // ---------- page skeleton ----------
  const $ = id => document.getElementById(id);
  document.title = cfg.title;
  const startScreen = el('section', 'pk-start');
  startScreen.id = 'start';
  const gameScreen = el('div', 'pk-game');
  gameScreen.id = 'game';
  gameScreen.hidden = true;
  const resultScreen = el('section', 'pk-results');
  resultScreen.id = 'result';
  resultScreen.hidden = true;
  document.body.append(startScreen, gameScreen, resultScreen);

  const settingsButton = button('⚙ 设置', () => openSettings(), 'pk-settings-toggle');
  settingsButton.setAttribute('aria-haspopup', 'dialog');
  const presentButton = button('投屏', () => setPresent(!presentOn, true), 'pk-present-toggle');
  presentButton.title = '大字号全屏，适合在教室投影或大屏上玩';
  const tools = el('div', 'pk-tools');
  tools.append(presentButton, settingsButton);

  // start screen
  const top = el('div', 'pk-top');
  const homeLink = () => {
    const a = el('a', 'pk-home', '← ');
    a.append(el('span', '', '返回游戏首页'));
    a.href = url('./');
    return a;
  };
  top.append(homeLink(), tools);
  const card = el('main', 'pk-card');
  const emblem = el('div', 'pk-emblem', cfg.emblem);
  emblem.setAttribute('aria-hidden', 'true');
  card.append(el('p', 'pk-eyebrow', `${PAGE.grade ? PAGE.grade + ' · ' : ''}${SUBJECT}小课堂`), emblem, el('h1', 'pk-title', cfg.title), el('p', 'pk-description', cfg.description));
  const stats = el('div', 'pk-stats');
  const statBank = el('strong', '', '…'), statPer = el('strong', '', String(PER_PLAYER)), statSeconds = el('strong', '', String(duration));
  for (const [strong, label] of [[statBank, '题库题量'], [statPer, '每人题数'], [statSeconds, '本局时长（秒）']]) {
    const box = el('div');
    box.append(strong, el('span', '', label));
    stats.append(box);
  }
  const modes = el('div', 'pk-modes');
  modes.setAttribute('role', 'radiogroup');
  modes.setAttribute('aria-label', '游戏模式');
  const modeButtons = {};
  for (const mode of ['pk', 'solo', 'mistakes']) {
    const b = button(modeLabel[mode], () => selectMode(mode), 'pk-mode');
    b.setAttribute('role', 'radio');
    modeButtons[mode] = b;
    modes.append(b);
  }
  const rules = el('div', 'pk-rules');
  const modeNote = el('p', 'pk-mode-note');
  rules.append(el('h2', '', '怎么玩'), el('p', '', cfg.instructions),
    el('p', 'pk-scoring', '答对每题加10分，连续答对额外加5分；答错扣5分，最低0分。提交后看解析，点击“下一题”继续。'), modeNote);
  let tipsBox = null;
  if (cfg.tips?.length) {
    // "**词**" marks a key word to highlight; everything stays text nodes.
    const list = el('ol', 'pk-tips');
    for (const tip of cfg.tips) {
      const li = el('li');
      tip.split('**').forEach((part, i) => part && li.append(i % 2 ? el('strong', 'pk-key', part) : document.createTextNode(part)));
      list.append(li);
    }
    tipsBox = el('section', 'pk-tips-box');
    tipsBox.setAttribute('aria-label', '方法技巧');
    tipsBox.append(el('h2', 'pk-tips-title', '💡 方法技巧'), list);
  }
  const go = button('正在加载题目…', () => startRound(), 'pk-primary');
  go.disabled = true;
  const loadError = el('p', 'pk-error');
  loadError.setAttribute('role', 'alert');
  if (TRIAL) card.append(el('p', 'pk-trial-note', '免费试玩：可以完整玩一局。点“开始”后，本游戏的试玩机会就用掉了。'));
  card.append(stats, modes, ...(tipsBox ? [tipsBox] : []), rules, go, loadError);
  startScreen.append(top, card);

  function selectMode(mode) {
    if (mode === 'mistakes' && !mistakes.ids().length) return;
    round.mode = mode;
    for (const [m, b] of Object.entries(modeButtons)) b.setAttribute('aria-checked', String(m === mode));
    const count = mistakes.ids().length;
    modeButtons.mistakes.textContent = count ? `错题重练（${count}）` : '错题重练';
    modeButtons.mistakes.disabled = !count;
    modeNote.textContent = {
      pk: '两位玩家同时作答，各自抽到不同的题目，得分高者获胜。',
      solo: `一个人练习${PER_PLAYER}题，答错的题会记入本机错题本。`,
      mistakes: `从本机错题本中抽题练习（最多${PER_PLAYER}题），答对后自动移出错题本。`,
    }[mode];
    statPer.textContent = mode === 'mistakes' ? String(Math.min(count, PER_PLAYER)) : String(PER_PLAYER);
    if (bank && !trial.claiming) go.textContent = (mode === 'pk' ? '开始PK' : '开始练习') + (TRIAL ? ' · 试玩' : '');
  }

  // game screen
  const header = el('header', 'pk-header');
  const timer = el('div', 'pk-timer');
  const timeValue = el('strong', '', String(duration));
  timer.append(timeValue, document.createTextNode(' 秒'));
  timer.setAttribute('aria-label', '剩余时间');
  header.append(el('h1', '', cfg.title), homeLink(), timer);
  const arena = el('main', 'pk-arena');
  gameScreen.append(header, arena);

  // ---------- settings dialog ----------
  const dialog = el('dialog', 'pk-dialog');
  dialog.setAttribute('aria-label', '游戏设置');
  dialog.innerHTML = '<div class="pk-dialog-head"><h2>游戏设置</h2><button type="button" class="pk-close" aria-label="关闭设置">×</button></div>' +
    '<label class="pk-label" for="pk-seconds">本局时长（秒）</label><input id="pk-seconds" type="number" min="30" max="1800" step="1" inputmode="numeric">' +
    '<div class="pk-presets"></div><p class="pk-help">30—1800秒；游戏开始后，本局时长固定。</p>' +
    '<label class="pk-switch"><span>背景音乐</span><input id="pk-music" type="checkbox"></label>' +
    '<label class="pk-switch"><span>点击与答题音效</span><input id="pk-fx" type="checkbox"></label>' +
    '<label class="pk-switch"><span>连击欢呼</span><input id="pk-voice" type="checkbox"></label>' +
    '<label class="pk-switch"><span>投屏模式（大字号全屏）</span><input id="pk-present" type="checkbox"></label>' +
    '<p class="pk-help">连续答对5题及以上时播放欢呼；关闭点击与答题音效时，欢呼也会静音。</p><p class="pk-error" role="status"></p>' +
    '<div class="pk-actions"><button type="button" class="pk-reset">恢复默认</button><button type="button" class="pk-save">保存设置</button></div>';
  document.body.append(dialog);
  const secondsInput = dialog.querySelector('#pk-seconds'), musicInput = dialog.querySelector('#pk-music'),
    fxInput = dialog.querySelector('#pk-fx'), voiceInput = dialog.querySelector('#pk-voice'),
    presentInput = dialog.querySelector('#pk-present'), settingsError = dialog.querySelector('.pk-error');
  for (const seconds of [60, 120, 150, 180, 300, 600]) {
    dialog.querySelector('.pk-presets').append(button(seconds + '秒', () => {
      secondsInput.value = seconds;
      settingsError.textContent = '';
    }, 'pk-preset'));
  }
  function updateSettingsLabels() {
    statSeconds.textContent = duration;
    if (!round.running) timeValue.textContent = duration;
    settingsButton.textContent = musicOn || fxOn ? '⚙ 设置' : '⚙ 设置 · 已静音';
  }
  function openSettings() {
    secondsInput.value = duration;
    secondsInput.disabled = round.running;
    dialog.querySelectorAll('.pk-preset,.pk-reset').forEach(b => (b.disabled = round.running));
    musicInput.checked = musicOn;
    fxInput.checked = fxOn;
    voiceInput.checked = voiceOn;
    presentInput.checked = presentOn;
    settingsError.textContent = '';
    dialog.showModal();
  }
  dialog.querySelector('.pk-close').onclick = () => dialog.close();
  dialog.querySelector('.pk-reset').onclick = () => (secondsInput.value = cfg.defaultSeconds);
  dialog.querySelector('.pk-save').onclick = () => {
    const n = Number(secondsInput.value);
    if (!round.running && !validSeconds(n)) {
      settingsError.textContent = '请输入30至1800之间的整数秒数。';
      secondsInput.focus();
      return;
    }
    if (!round.running) duration = n;
    musicOn = musicInput.checked;
    fxOn = fxInput.checked;
    voiceOn = voiceInput.checked;
    saveSettings();
    updateSettingsLabels();
    audio.settingsChanged();
    dialog.close();
    if (presentInput.checked !== presentOn) setPresent(presentInput.checked, true);
  };
  window.addEventListener('storage', e => {
    if (e.key !== SETTINGS_KEY) return;
    const s = readStore(SETTINGS_KEY, {});
    musicOn = s.music !== false;
    fxOn = s.fx !== false;
    voiceOn = s.voice !== false;
    if ((s.present === true) !== presentOn) setPresent(s.present === true, false);
    if (!round.running && validSeconds(s.durations?.[cfg.id])) duration = s.durations[cfg.id];
    updateSettingsLabels();
    audio.settingsChanged();
  });

  // ---------- 投屏 mode: bigger type everywhere, plus fullscreen when the teacher turns it on ----------
  let ownFullscreen = false;
  function setPresent(on, fromClick) {
    presentOn = on;
    document.documentElement.classList.toggle('pk-present', on);
    presentButton.setAttribute('aria-pressed', String(on));
    presentButton.textContent = on ? '退出投屏' : '投屏';
    if (!fromClick) return;
    saveSettings();
    const fs = document.fullscreenElement;
    if (on && !fs && document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().then(() => (ownFullscreen = true)).catch(() => {});
    } else if (!on && fs && ownFullscreen) {
      document.exitFullscreen().catch(() => {});
    }
  }
  document.addEventListener('fullscreenchange', () => {
    // Esc leaves fullscreen; leave 投屏 with it so the page is not stuck at projector size.
    if (!document.fullscreenElement && ownFullscreen) {
      ownFullscreen = false;
      if (presentOn) setPresent(false, true);
    }
  });
  setPresent(presentOn, false);

  // ---------- free trial and the admin's WeChat QR code ----------
  const trial = { claimed: false, claiming: false, done: false };
  const contact = el('dialog', 'pk-dialog pk-contact');
  contact.setAttribute('aria-label', '获取授权码');
  const contactText = el('p', '');
  const qr = el('img');
  qr.alt = '管理员微信二维码';
  qr.loading = 'lazy';
  qr.src = url(QR);
  const contactHead = el('div', 'pk-dialog-head');
  contactHead.append(el('h2', '', '获取授权码'), button('×', () => contact.close(), 'pk-close'));
  contactHead.lastChild.setAttribute('aria-label', '关闭');
  const contactActions = el('div', 'pk-actions');
  const contactHome = el('a', 'secondary pk-home-button', '返回游戏首页');
  contactHome.href = url('./');
  contactActions.append(contactHome);
  contact.append(contactHead, contactText, qr, el('p', 'pk-help', '在手机上可以长按二维码识别。'), contactActions);
  contact.addEventListener('click', e => e.target === contact && contact.close());
  document.body.append(contact);
  function showContact(reason) {
    contactText.textContent = (reason || (TRIAL ? '试玩结束啦！' : '')) + '微信扫一扫，添加管理员为好友，获取授权码，解锁全部题目。';
    if (!contact.open) contact.showModal();
  }
  async function claimTrial() {
    if (trial.claiming) return;
    trial.claiming = true;
    go.disabled = true;
    go.textContent = '正在开始…';
    let refused = false;
    try {
      const token = await fetch(TRIAL.csrf, { cache: 'no-store', credentials: 'same-origin' }).then(r => r.json());
      const r = await fetch(TRIAL.start, { method: 'POST', cache: 'no-store', credentials: 'same-origin', headers: { 'X-CSRF-Token': token.csrf } });
      refused = r.status === 403;
    } catch {} // network trouble: let the child play rather than block the round
    trial.claiming = false;
    if (refused) {
      trial.done = true;
      go.textContent = '试玩已结束';
      showContact('本游戏的试玩已经用过了。');
      return;
    }
    trial.claimed = true;
    go.disabled = false;
    selectMode(round.mode);
    startRound();
  }

  // ---------- the round ----------
  function startRound() {
    if (round.running || !bank) return;
    if (TRIAL && trial.done) return showContact('本游戏的试玩已经结束。');
    if (TRIAL && !trial.claimed) return void claimTrial();
    if (round.mode === 'mistakes' && !mistakes.ids().length) selectMode('solo');
    audio.unlock();
    let draws;
    try {
      draws = drawQuestions(round.mode === 'pk' ? 2 : 1).filter(questions => questions.length);
    } catch (error) {
      loadError.textContent = error.message;
      return;
    }
    if (!draws.length) return;
    duration = validSeconds(duration) ? duration : cfg.defaultSeconds;
    round.running = true;
    round.ended = false;
    round.deadline = Date.now() + duration * 1000;
    round.players = draws.map(questions => ({ questions, index: 0, score: 0, combo: 0, correct: 0, wrong: [], a: null, view: null,
      history: [], result: null, notice: '' }));
    arena.replaceChildren();
    arena.classList.toggle('solo', round.players.length === 1);
    round.players.forEach((s, p) => {
      const panel = el('section', 'pk-player' + (p ? ' second' : ''));
      panel.setAttribute('aria-label', round.players.length === 1 ? '练习' : NAMES[p]);
      const head = el('div', 'pk-player-head');
      const score = el('span', 'pk-score', '0');
      score.id = 'score-' + p;
      head.append(el('h2', '', round.players.length === 1 ? modeLabel[round.mode] : NAMES[p]), score);
      const body = el('div', 'pk-body');
      body.id = 'body-' + p;
      panel.append(head, body);
      arena.append(panel);
      load(p);
    });
    startScreen.hidden = true;
    resultScreen.hidden = true;
    gameScreen.hidden = false;
    header.append(tools);
    document.body.classList.add('pk-playing');
    audio.resetCheer();
    audio.resume();
    tick();
    clearInterval(round.timer);
    round.timer = setInterval(tick, 200);
    window.scrollTo(0, 0);
  }
  const current = p => round.players[p]?.questions[round.players[p].index];
  function expired() {
    if (!round.running) return true;
    if (Date.now() < round.deadline) return false;
    finish();
    return true;
  }
  function tick() {
    if (!round.running) return;
    timeValue.textContent = Math.max(0, Math.ceil((round.deadline - Date.now()) / 1000));
    expired();
  }
  function editable(p) {
    return !expired() && !!current(p) && !round.players[p].result;
  }
  function load(p) {
    const s = round.players[p], q = current(p);
    s.history = [];
    s.result = null;
    s.notice = '';
    s.view = {};
    s.a = q ? renderer.init(q, contextFor(p)) : null;
    render(p);
    if (round.players.every(x => x.index >= x.questions.length)) finish();
  }
  function contextFor(p) {
    const s = round.players[p];
    return {
      player: p,
      ui: bank.ui || {},
      view: s.view,
      get locked() {
        return !!s.result || !round.running;
      },
      get result() {
        return s.result;
      },
      change(fn) {
        if (!editable(p)) return;
        s.history.push(clone(s.a));
        fn(s.a);
        s.notice = '';
        render(p);
      },
      mutate(fn) {
        if (editable(p)) fn(s.a);
      },
      notice(text) {
        s.notice = text;
        const node = $('notice-' + p);
        if (node) node.textContent = text;
      },
    };
  }
  function undo(p) {
    const s = round.players[p];
    if (!editable(p) || !s.history.length) return;
    s.a = s.history.pop();
    render(p);
  }
  function clearAnswer(p) {
    const s = round.players[p];
    if (!editable(p)) return;
    s.history.push(clone(s.a));
    const view = s.view;
    s.a = renderer.init(current(p), { ...contextFor(p), view: {} });
    s.view = view; // keep the shuffled order the player is looking at
    render(p);
  }
  function submit(p) {
    if (!editable(p)) return;
    const s = round.players[p], q = current(p), ctx = contextFor(p);
    const answer = renderer.value(q, s.a, ctx);
    if (answer.incomplete) {
      ctx.notice(answer.incomplete);
      return;
    }
    const correct = !!renderer.check(q, answer.value, ctx);
    s.result = { correct, value: answer.value };
    if (correct) {
      s.combo++;
      s.correct++;
      s.score += 10 + (s.combo > 1 ? 5 : 0);
      if (round.mode === 'mistakes') mistakes.remove(q);
    } else {
      s.combo = 0;
      s.score = Math.max(0, s.score - 5);
      s.wrong.push(q);
      mistakes.add(q, renderer);
    }
    audio.result(correct, p, s.combo);
    render(p);
    $('next-' + p)?.focus({ preventScroll: true });
  }
  function next(p) {
    const s = round.players[p];
    if (expired() || !s.result || !current(p)) return;
    s.index++;
    load(p);
  }
  function render(p) {
    const s = round.players[p], q = current(p), body = $('body-' + p);
    if (!body) return;
    $('score-' + p).textContent = s.score;
    body.replaceChildren();
    if (!q) {
      body.append(el('p', 'pk-waiting', round.players.length > 1 ? `本轮${s.questions.length}题已完成，等待另一位玩家。` : '本轮已完成。'));
      return;
    }
    const ctx = contextFor(p);
    body.append(el('p', 'pk-source', `第 ${s.index + 1} / ${s.questions.length} 题 · ${q.source.label}`));
    if (bank.ui?.task) body.append(el('p', 'pk-task', bank.ui.task));
    if (bank.ui?.instruction) body.append(el('p', 'pk-instruction', bank.ui.task ? `注意：${bank.ui.instruction}` : bank.ui.instruction));
    const question = renderer.render(q, s.a, ctx);
    body.append(question);
    const notice = el('p', 'pk-notice', s.notice);
    notice.id = 'notice-' + p;
    notice.setAttribute('role', 'status');
    body.append(notice);
    if (s.result) body.append(feedback(q, s));
    const controls = el('div', 'pk-controls');
    if (!s.result && round.running) {
      if (renderer.history !== false) controls.append(button('撤回', () => undo(p), 'secondary'), button('清空', () => clearAnswer(p), 'secondary'));
      controls.append(button('提交答案', () => submit(p), 'primary'));
    } else if (round.running) {
      const last = s.index === s.questions.length - 1;
      const b = button(last ? '完成本轮' : '下一题', () => next(p), 'primary next');
      b.id = 'next-' + p;
      controls.append(b);
    }
    body.append(controls);
    if (!round.running) body.querySelectorAll('button,input').forEach(b => (b.disabled = true));
    renderer.afterRender?.(q, s.a, ctx, question);
  }
  function feedback(q, s) {
    const box = el('div', 'pk-feedback ' + (s.result.correct ? 'correct' : 'wrong'));
    box.setAttribute('role', 'status');
    box.append(el('strong', '', s.result.correct ? (s.combo > 1 ? '答对了！+15分' : '答对了！+10分') : '再想一想，看看解析'));
    box.append(el('p', '', '正确答案：' + renderer.solution(q)));
    box.append(el('p', '', q.explanation));
    if (q.source.detail) {
      const details = el('details');
      details.append(el('summary', '', q.source.detailTitle), el('p', '', q.source.detail));
      box.append(details);
    }
    return box;
  }
  function finish() {
    if (!round.running) return;
    round.running = false;
    round.ended = true;
    if (TRIAL) trial.done = true;
    clearInterval(round.timer);
    audio.stop();
    if (Date.now() >= round.deadline) timeValue.textContent = '0';
    round.players.forEach((_, p) => render(p));
    document.body.classList.remove('pk-playing');
    showResults();
  }
  function showResults() {
    resultScreen.replaceChildren();
    const players = round.players;
    let title;
    if (players.length === 1) title = round.mode === 'mistakes' ? '错题重练完成！' : '练习完成！';
    else title = players[0].score === players[1].score ? '平局！' : players[0].score > players[1].score ? '玩家一获胜！' : '玩家二获胜！';
    resultScreen.append(el('h2', '', title));
    const summary = el('div', 'pk-summary');
    players.forEach((s, p) => {
      const box = el('div', 'pk-summary-player' + (p ? ' second' : ''));
      box.append(el('span', '', players.length > 1 ? NAMES[p] : modeLabel[round.mode]), el('strong', '', s.score + ' 分'),
        el('span', '', `答对 ${s.correct} 题 · 答错 ${s.wrong.length} 题`));
      summary.append(box);
    });
    resultScreen.append(summary);
    players.forEach((s, p) => {
      if (!s.wrong.length) return;
      const list = el('details', 'pk-wrong-list');
      list.open = players.length === 1;
      list.append(el('summary', '', `${players.length > 1 ? NAMES[p] + '的' : '本局'}错题（${s.wrong.length}）`));
      for (const q of s.wrong) {
        const item = el('div', 'pk-wrong-item');
        item.append(el('p', 'pk-wrong-q', renderer.describe(q)), el('p', '', '正确答案：' + renderer.solution(q)), el('p', 'pk-wrong-why', q.explanation));
        list.append(item);
      }
      resultScreen.append(list);
    });
    const note = el('p', 'pk-help', '答错的题已记入本机错题本，可在开始页选择“错题重练”，或在游戏首页查看错题本。');
    if (players.some(s => s.wrong.length)) resultScreen.append(note);
    const actions = el('div', 'pk-result-actions');
    if (TRIAL) {
      const back = el('a', 'secondary pk-home-button', '返回游戏首页');
      back.href = url('./');
      actions.append(button('获取授权码', () => showContact(), 'primary'), back);
      resultScreen.append(actions);
      resultScreen.hidden = false;
      resultScreen.scrollIntoView({ behavior: 'smooth', block: 'start' });
      setTimeout(() => showContact(), 1200);
      return;
    }
    actions.append(button('再来一局', () => {
      if (round.mode === 'mistakes' && !mistakes.ids().length) selectMode('solo');
      startRound();
    }, 'primary'), button('返回开始页', () => {
      gameScreen.hidden = true;
      resultScreen.hidden = true;
      startScreen.hidden = false;
      top.append(tools);
      selectMode(round.mode === 'mistakes' && !mistakes.ids().length ? 'solo' : round.mode);
      window.scrollTo(0, 0);
    }, 'secondary'));
    resultScreen.append(actions);
    resultScreen.hidden = false;
    resultScreen.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) audio.stop();
    else {
      tick();
      audio.resume();
    }
  });
  window.addEventListener('pageshow', () => {
    tick();
    audio.resume();
  });
  window.addEventListener('pagehide', () => audio.stop());

  // ---------- boot ----------
  async function boot() {
    renderer = renderers[cfg.type];
    try {
      const response = await fetch(url(`games/${cfg.id}/bank.json`), { cache: 'no-store' });
      if (response.status === 403) throw Error(TRIAL ? '本游戏的试玩已经结束，扫码添加管理员微信获取授权码。' : '授权已失效，请返回游戏首页重新激活。');
      if (!response.ok) throw Error('题目加载失败，请刷新页面重试。');
      bank = await response.json();
      if (!renderer || bank.type !== cfg.type) throw Error('游戏组件版本不一致，请刷新页面重试。');
    } catch (error) {
      go.textContent = '无法开始';
      loadError.textContent = error instanceof TypeError ? '网络连接失败，请检查网络后刷新页面。' : error.message;
      return;
    }
    statBank.textContent = bank.total ?? bank.questions.length;
    go.disabled = false;
    selectMode(location.hash === '#mistakes' && mistakes.ids().length ? 'mistakes' : 'pk');
  }
  updateSettingsLabels();
  selectMode('pk');
  window.PK = {
    register(type, r) {
      renderers[type] = r;
    },
    el, button, shuffle, svg,
    figures: {},
    // test hooks
    _round: round, _submit: submit, _next: next, _current: current,
  };
  document.addEventListener('DOMContentLoaded', boot);
})();
