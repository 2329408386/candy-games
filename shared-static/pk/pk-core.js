// Shared engine for every PK game on both sites.
//
// A game page is a shell holding <script id="game-config"> plus this file and one question-type renderer from
// ./types/. The engine loads the licensed bank (../games/<id>/bank.json), draws questions, runs the round
// (one player or two, one shared deadline), scores answers, records mistakes and shows the results.
// Renderers register with PK.register(type, renderer); see the contract above `register` below.
// The server injects window.PK_PAGE = {grade, trial, score}: trial is null for licensed browsers, or {start, csrf} URLs when
// this browser may play one free round of the game (starting the round uses it up). score ({start, finish, csrf} URLs,
// licensed browsers only) reports each solo round's right/wrong sequence for the class rankings. room (licensed only)
// holds the online PK room URLs (CODE stands for the room code); see "online PK rooms" below and core/rooms.py.
(() => {
  'use strict';
  const cfg = JSON.parse(document.getElementById('game-config').textContent);
  const PAGE = window.PK_PAGE || {};
  const TRIAL = PAGE.trial || null;
  const ROOM = PAGE.room || null;
  const QR = 'static/shared/contact/wechat-qr.0736f95e.jpeg';
  const SUBJECT = { chinese: '语文', math: '数学', english: '英语' }[cfg.site];
  const PER_PLAYER = 20;
  const NAMES = ['玩家一', '玩家二'];
  const base = new URL('../', document.baseURI); // site root for /games/<id>, /math/games/<id>, /english/games/<id> and legacy /<name>.html
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
  // Copies text: the Clipboard API needs HTTPS, so plain-HTTP pages fall back to a hidden textarea + execCommand.
  async function copyText(text) {
    try {
      if (window.isSecureContext && navigator.clipboard) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch {}
    const area = el('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0';
    document.body.append(area);
    area.select();
    area.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand('copy'); } catch {}
    area.remove();
    return ok;
  }
  let toastTimer = 0;
  function toast(text, ok = true) {
    let box = document.querySelector('.pk-toast');
    if (!box) {
      box = el('div', 'pk-toast');
      box.setAttribute('role', 'status');
      document.body.append(box);
    }
    box.textContent = (ok ? '✓ ' : '') + text;
    box.classList.toggle('bad', !ok);
    box.classList.remove('show');
    void box.offsetWidth;
    box.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => box.classList.remove('show'), 2200);
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
  // Banks with ui.units can be played one unit at a time ('' = every unit, the 期末综合 draw).
  let unit = typeof settings.units?.[cfg.id] === 'string' ? settings.units[cfg.id] : '';
  function saveSettings() {
    settings = readStore(SETTINGS_KEY, {});
    settings.music = musicOn;
    settings.fx = fxOn;
    settings.voice = voiceOn;
    settings.present = presentOn;
    if (!settings.durations || typeof settings.durations !== 'object' || Array.isArray(settings.durations)) settings.durations = {};
    // Only a changed time is remembered, so a new default for the game still reaches everybody else.
    if (duration === cfg.defaultSeconds) delete settings.durations[cfg.id];
    else settings.durations[cfg.id] = duration;
    if (!settings.units || typeof settings.units !== 'object' || Array.isArray(settings.units)) settings.units = {};
    if (unit) settings.units[cfg.id] = unit;
    else delete settings.units[cfg.id];
    writeStore(SETTINGS_KEY, settings);
  }

  // ---------- mistake book: { "<game>/<id>": {game, id, text, answer, count, last} } ----------
  const MISTAKES_KEY = cfg.site + '-mistakes-v1';
  const MISTAKE_LIMIT = 300;
  const mistakes = {
    all: () => readStore(MISTAKES_KEY, {}),
    // Only ids still in the bank count, so removed or replaced questions drop out of 错题重练.
    ids: () => Object.values(mistakes.all()).filter(m => m.game === cfg.id && (!bank || bank.questions.some(q => q.id === m.id))).map(m => m.id),
    add(q, renderer, why) {
      const book = mistakes.all();
      const key = cfg.id + '/' + q.id;
      const old = book[key];
      book[key] = { game: cfg.id, id: q.id, text: renderer.describe(q).slice(0, 160), answer: renderer.solution(q).slice(0, 160),
        count: (old?.count || 0) + 1, last: Date.now() };
      if (why) book[key].why = why.slice(0, 200);
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
    const speech = new Audio();
    speech.onended = speech.onerror = () => duck();
    let voice = null, volumeFrame = 0, unlocked = false, lastClick = 0, lastVoice = -Infinity;
    function duck() {
      const target = (voice && !voice.paused) || !speech.paused ? 0.06 : playing.size ? 0.12 : 0.45;
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
        speech.pause();
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
      // Listening questions: plays even with effects muted (the recording is the question, not decoration).
      speak(src) {
        try {
          speech.pause();
          if (!speech.src.endsWith(src)) speech.src = src;
          speech.currentTime = 0;
          speech.play().then(duck).catch(duck);
          duck();
        } catch {}
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
  //   diagnose(q, value, ctx) -> optional: for a wrong answer, what exactly is wrong with it (shown with the answer,
  //                              in the round's mistakes and in the mistake book)
  //   history                 -> false hides 撤回/清空;  afterRender(q, a, ctx, node) -> optional, runs after insertion
  //   submitButton            -> false hides 提交答案: the renderer calls ctx.submit() itself (读句子 after scoring)
  const renderers = {};
  let bank = null, renderer = null;

  // ---------- round state ----------
  const round = { running: false, ended: false, deadline: 0, timer: 0, mode: 'pk', players: [] };
  const modeLabel = { pk: '双人PK', solo: '单人练习', mistakes: '错题重练', online: '联网PK' };

  function drawQuestions(count) {
    const questions = bank.questions;
    if (round.mode === 'mistakes') {
      const wanted = new Set(mistakes.ids());
      return [shuffle(questions.filter(q => wanted.has(q.id))).slice(0, PER_PLAYER)];
    }
    if (unit) {
      const pool = shuffle(unitPool()), per = perPlayer(count);
      if (!per) throw Error('本单元题目不足，请换一个单元。');
      return Array.from({ length: count }, (_, p) => pool.slice(p * per, (p + 1) * per));
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

  // Unit rounds draw only that unit's questions: up to 20 each, fewer when two players split a small unit.
  const unitPool = () => bank.questions.filter(q => q.unit === unit);
  const perPlayer = count => (bank && unit ? Math.min(PER_PLAYER, Math.floor(unitPool().length / count)) : PER_PLAYER);
  function showUnits() {
    const units = bank.ui?.units;
    // A trial bank only holds one round's questions, so it always plays the 期末综合 draw.
    if (!units?.length || bank.total !== undefined) {
      unit = '';
      return;
    }
    if (unit && !units.includes(unit)) unit = '';
    unitPicker.replaceChildren();
    for (const value of ['', ...units]) {
      const b = button(value ? value : (bank.ui.allUnitsLabel || '期末综合'), () => {
        unit = value;
        saveSettings();
        showUnits();
        selectMode(round.mode);
      }, 'pk-unit');
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', String(value === unit));
      if (value) b.title = bank.ui.unitNames?.[value] || value;
      unitPicker.append(b);
    }
    unitPicker.hidden = false;
    unitNote.hidden = false;
    unitNote.textContent = unit ? `单元闯关：${bank.ui.unitNames?.[unit] || unit}（只抽本单元的题）` : '期末综合：各单元的题按比例混合抽取，适合期末复习。';
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
  const unitPicker = el('div', 'pk-units');
  unitPicker.setAttribute('role', 'radiogroup');
  unitPicker.setAttribute('aria-label', '练习范围');
  unitPicker.hidden = true;
  const unitNote = el('p', 'pk-unit-note');
  unitNote.hidden = true;
  const modes = el('div', 'pk-modes');
  modes.setAttribute('role', 'radiogroup');
  modes.setAttribute('aria-label', '游戏模式');
  const modeButtons = {};
  // Listening games (cfg.noPk) have no 双人PK: two players share one speaker, so each would hear the other's recording.
  const modeList = (ROOM ? ['pk', 'solo', 'mistakes', 'online'] : ['pk', 'solo', 'mistakes']).filter(m => !(cfg.noPk && m === 'pk'));
  modes.classList.add({ 2: 'two', 3: 'three', 4: 'four' }[modeList.length]);
  for (const mode of modeList) {
    const b = button(modeLabel[mode], () => selectMode(mode), 'pk-mode');
    b.setAttribute('role', 'radio');
    modeButtons[mode] = b;
    modes.append(b);
  }
  const rules = el('div', 'pk-rules');
  const modeNote = el('p', 'pk-mode-note');
  rules.append(el('h2', '', '怎么玩'));
  if (cfg.highlight) rules.append(el('p', 'pk-highlight', cfg.highlight)); // one point the player must not miss
  rules.append(el('p', '', cfg.instructions),
    el('p', 'pk-scoring', '答对每题加10分，连续答对额外加5分；答错扣5分，最低0分。提交后自动进入下一题（答对停1秒，答错停2秒看正确答案）。'), modeNote);
  let tipsBox = null;
  if (cfg.tips?.length) {
    // "**词**" marks a key word to highlight; everything stays text nodes.
    const list = el('ol', 'pk-tips');
    const richText = (parent, text) =>
      text.split('**').forEach((part, i) => part && parent.append(i % 2 ? el('strong', 'pk-key', part) : document.createTextNode(part)));
    for (const tip of cfg.tips) {
      const li = el('li');
      richText(li, typeof tip === 'string' ? tip : tip.text);
      if (tip.items?.length) {
        const sub = el('ul', 'pk-subtips');
        for (const item of tip.items) {
          const chip = el('li');
          richText(chip, item);
          sub.append(chip);
        }
        li.append(sub);
      }
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
  const onlinePanel = el('section', 'pk-online');
  onlinePanel.hidden = true;
  card.append(stats, unitPicker, unitNote, modes, ...(tipsBox ? [tipsBox] : []), rules, go, onlinePanel, loadError);
  startScreen.append(top, card);

  function selectMode(mode) {
    if (mode === 'pk' && cfg.noPk) mode = 'solo';
    if (mode === 'mistakes' && !mistakes.ids().length) return;
    round.mode = mode;
    for (const [m, b] of Object.entries(modeButtons)) b.setAttribute('aria-checked', String(m === mode));
    const count = mistakes.ids().length;
    modeButtons.mistakes.textContent = count ? `错题重练（${count}）` : '错题重练';
    modeButtons.mistakes.disabled = !count;
    modeNote.textContent = {
      pk: '两位玩家同时作答，各自抽到不同的题目，得分高者获胜。',
      solo: `一个人练习${PER_PLAYER}题，答错的题会记入本机错题本。` +
        (cfg.noPk ? (cfg.noPkReason || '听力题要听录音，两人共用一个喇叭会互相干扰，') + (ROOM ? '所以没有双人PK；想和同学比赛，请用「联网PK」。' : '所以没有双人PK。') : ''),
      mistakes: `从本机错题本中抽题练习（最多${PER_PLAYER}题），答对后自动移出错题本，每改对一道 +1 分。`,
      online: `和同学各用自己的设备，同时答同一套${PER_PLAYER}道题，实时比分；2到10人一起玩，前三名有名次奖励（第一名 +20，第二名 +10，第三名 +5；只有两人时第一名 +10）。`,
    }[mode];
    go.hidden = mode === 'online';
    onlinePanel.hidden = mode !== 'online';
    if (mode === 'online') online.showPanel();
    else online.hidePanel();
    statPer.textContent = mode === 'mistakes' ? String(Math.min(count, PER_PLAYER)) : String(perPlayer(mode === 'pk' ? 2 : 1));
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
  const onlineBar = el('div', 'pk-online-bar');
  onlineBar.hidden = true;
  onlineBar.setAttribute('aria-label', '实时比分');
  gameScreen.append(header, onlineBar, arena);

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

  // ---------- class rankings: a ticket when a solo round starts, the right/wrong sequence when it ends ----------
  const SCORE = PAGE.score || null;
  let scoreToken = null, csrfValue = '';
  const csrfToken = () => (scoreToken ||= fetch(SCORE.csrf, { cache: 'no-store', credentials: 'same-origin' }).then(r => r.json())
    .then(d => (csrfValue = d.csrf)).catch(e => { scoreToken = null; throw e; }));
  // POSTs JSON (GET when body is undefined). Errors carry .status (0 = network) and the server's message.
  async function scorePost(path, body) {
    const opts = { cache: 'no-store', credentials: 'same-origin' };
    if (body !== undefined) {
      Object.assign(opts, { method: 'POST', headers: { 'X-CSRF-Token': await csrfToken(), 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    }
    let r;
    try {
      r = await fetch(path, opts);
    } catch {
      throw Object.assign(Error('网络连接失败，正在重试…'), { status: 0 });
    }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(Error(data.error || '请求失败，请稍后重试'), { status: r.status, data });
    return data;
  }

  // ---------- the round ----------
  // preset = {draws, deadline} starts an online round with the room's questions and the server's deadline.
  function startRound(preset) {
    if (round.running || !bank) return;
    let draws = preset?.draws;
    if (!preset) {
      if (TRIAL && trial.done) return showContact('本游戏的试玩已经结束。');
      if (TRIAL && !trial.claimed) return void claimTrial();
      if (round.mode === 'online') return;
      if (round.mode === 'mistakes' && !mistakes.ids().length) selectMode('solo');
      audio.unlock();
      try {
        draws = drawQuestions(round.mode === 'pk' ? 2 : 1).filter(questions => questions.length);
      } catch (error) {
        loadError.textContent = error.message;
        return;
      }
    }
    if (!draws.length) return;
    duration = validSeconds(duration) ? duration : cfg.defaultSeconds;
    round.running = true;
    round.ended = false;
    round.deadline = preset ? preset.deadline : Date.now() + duration * 1000;
    round.players = draws.map(questions => ({ questions, index: 0, score: 0, combo: 0, correct: 0, wrong: [], whys: {}, marks: [], a: null, view: null,
      history: [], result: null, notice: '' }));
    round.ticket = SCORE && (round.mode === 'solo' || round.mode === 'mistakes')
      ? scorePost(SCORE.start, { mode: round.mode }).then(d => d.round, () => null) : null;
    if (SCORE && round.mode === 'pk') scorePost(SCORE.start, { mode: 'pk' }).catch(() => {}); // only counted for the back office's statistics
    round.report = null;
    arena.replaceChildren();
    arena.classList.toggle('solo', round.players.length === 1);
    round.players.forEach((s, p) => {
      const panel = el('section', 'pk-player' + (p ? ' second' : ''));
      panel.setAttribute('aria-label', round.players.length === 1 ? '练习' : NAMES[p]);
      const head = el('div', 'pk-player-head');
      const score = el('span', 'pk-score', '0');
      score.id = 'score-' + p;
      head.append(el('h2', '', round.players.length === 1 ? modeLabel[round.mode] : NAMES[p]), score);
      if (round.mode === 'online') head.firstChild.textContent = `联网PK · 房间 ${online.code}`;
      else if (unit && round.mode !== 'mistakes') head.firstChild.textContent += ' · ' + unit;
      const body = el('div', 'pk-body');
      body.id = 'body-' + p;
      panel.append(head, body);
      arena.append(panel);
      load(p);
    });
    startScreen.hidden = true;
    lobbyScreen.hidden = true;
    resultScreen.hidden = true;
    gameScreen.hidden = false;
    onlineBar.hidden = round.mode !== 'online';
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
  // After an answer the round moves on by itself: 1 s to enjoy a right answer, 2 s to see the correct one.
  const ADVANCE_MS = { right: 1000, wrong: 2000 };
  function load(p) {
    const s = round.players[p], q = current(p);
    clearTimeout(s.advance);
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
      mode: round.mode,
      players: round.players.length,
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
      submit() {
        submit(p);
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
    if (!correct && renderer.diagnose) {
      try { s.result.why = renderer.diagnose(q, answer.value, ctx) || ''; } catch { s.result.why = ''; }
    }
    s.marks.push(correct);
    if (round.mode === 'online') online.report(s.marks.length - 1, correct);
    if (correct) {
      s.combo++;
      s.correct++;
      s.score += 10 + (s.combo > 1 ? 5 : 0);
      if (round.mode === 'mistakes') mistakes.remove(q);
    } else {
      s.combo = 0;
      s.score = Math.max(0, s.score - 5);
      s.wrong.push(q);
      s.whys[q.id] = s.result.why;
      mistakes.add(q, renderer, s.result.why);
    }
    audio.result(correct, p, s.combo);
    s.wait = correct ? ADVANCE_MS.right : ADVANCE_MS.wrong;
    render(p);
    clearTimeout(s.advance);
    s.advance = setTimeout(() => next(p), s.wait);
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
      body.append(el('p', 'pk-waiting', round.mode === 'online' ? `${s.questions.length}题全部答完！等其他同学答完或时间到，就公布名次。`
        : round.players.length > 1 ? `本轮${s.questions.length}题已完成，等待另一位玩家。` : '本轮已完成。'));
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
      if (renderer.submitButton !== false) controls.append(button('提交答案', () => submit(p), 'primary'));
    } else if (round.running) {
      const last = s.index === s.questions.length - 1;
      const bar = el('div', 'pk-advance');
      bar.style.setProperty('--wait', (s.wait || ADVANCE_MS.right) + 'ms');
      bar.append(el('i'), el('span', '', last ? '马上完成本轮…' : '马上进入下一题…'));
      controls.append(bar);
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
    if (!s.result.correct && s.result.why) box.append(el('p', 'pk-why', '✏️ 哪里没写好：' + s.result.why));
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
    round.players.forEach(s => clearTimeout(s.advance));
    round.running = false;
    round.ended = true;
    if (TRIAL) trial.done = true;
    if (round.ticket) {
      const marks = round.players[0].marks;
      round.report = round.ticket.then(ticket => ticket && scorePost(SCORE.finish, { round: ticket, answers: marks })).catch(() => null);
    }
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
    if (round.mode === 'online') title = '联网PK结束！';
    else if (players.length === 1) title = round.mode === 'mistakes' ? '错题重练完成！' : '练习完成！';
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
    if (round.mode === 'online') resultScreen.append(online.resultsBox());
    if (round.report) {
      const line = el('p', 'pk-help pk-rank-note', '正在计入排行…');
      line.setAttribute('role', 'status');
      resultScreen.append(line);
      round.report.then(d => {
        if (!d) line.textContent = '这一局的成绩没能提交（网络不稳定），不影响继续练习。';
        else if (!d.recorded) line.textContent = d.reason;
        else {
          const how = d.mode === 'mistakes' ? `（改对 ${d.points} 道错题，每道 +1 分）` : `（本局 ${players[0].score} 分的十分之一）`;
          let text = `已计入排行 +${d.points} 分${how}`;
          if (d.promoted) text += `　🎉 晋级 ${d.promoted}！`;
          else if (d.stars > 0) text += `　⭐ +${d.stars} 星`;
          if (d.rank) text += `　段位：${d.rank.name}${d.rank.slots ? ' ' + '★'.repeat(d.rank.stars) + '☆'.repeat(d.rank.slots - d.rank.stars) : ' ★×' + d.rank.stars}，再得 ${d.rank.toNext} 分加一星`;
          if (!d.in_class) text += '。在首页右上角的「我的」里加入班级，还能和同学比班级排名。';
          line.textContent = text;
        }
      });
    }
    players.forEach((s, p) => {
      if (!s.wrong.length) return;
      const list = el('details', 'pk-wrong-list');
      list.open = players.length === 1;
      list.append(el('summary', '', `${players.length > 1 ? NAMES[p] + '的' : '本局'}错题（${s.wrong.length}）`));
      for (const q of s.wrong) {
        const item = el('div', 'pk-wrong-item');
        item.append(el('p', 'pk-wrong-q', renderer.describe(q)), el('p', '', '正确答案：' + renderer.solution(q)));
        if (s.whys?.[q.id]) item.append(el('p', 'pk-why', '✏️ 哪里没写好：' + s.whys[q.id]));
        item.append(el('p', 'pk-wrong-why', q.explanation));
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
    if (round.mode === 'online') {
      actions.append(button('再开一个房间', () => online.create(), 'primary'), button('返回开始页', () => online.leave(), 'secondary'));
      resultScreen.append(actions);
      resultScreen.hidden = false;
      resultScreen.scrollIntoView({ behavior: 'smooth', block: 'start' });
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

  // ---------- online PK rooms: everybody on their own device, same questions, live scores (core/rooms.py) ----------
  // The page polls the room every 2 s; answers are graded here and reported one by one, in order.
  const lobbyScreen = el('section', 'pk-start pk-lobby');
  lobbyScreen.hidden = true;
  document.body.insertBefore(lobbyScreen, gameScreen);
  const online = (() => {
    const POLL = 2000;
    const o = { code: '', state: null, offset: 0, timer: 0, fails: 0, queue: Promise.resolve(), lastPost: 0, begun: false, panelTimer: 0 };
    const roomUrl = kind => ROOM[kind].replace('CODE', o.code);
    const serverNow = () => Date.now() + o.offset;
    let index = null;
    const question = id => (index ||= new Map(bank.questions.map(q => [String(q.id), q]))).get(String(id));
    function avatar(p, cls = '') {
      const a = el('span', `pk-av c${p.color} ${cls}`);
      a.setAttribute('aria-hidden', 'true');
      if (p.avatar) {
        const img = el('img');
        img.src = p.avatar;
        img.alt = '';
        a.append(img);
      } else a.textContent = p.initial;
      return a;
    }
    function setUrl(code) {
      try {
        const u = new URL(location.href);
        if (code) u.searchParams.set('room', code);
        else u.searchParams.delete('room');
        history.replaceState(null, '', u);
      } catch {}
    }

    // ---- start-screen panel: create, join by code, rooms classmates are waiting in ----
    const panelError = el('p', 'pk-error');
    panelError.setAttribute('role', 'alert');
    const createButton = button('创建房间', () => create(), 'pk-primary');
    const codeInput = el('input', 'pk-online-code');
    Object.assign(codeInput, { placeholder: '输入6位房间码', maxLength: 6, inputMode: 'numeric', autocomplete: 'off' });
    codeInput.setAttribute('aria-label', '房间码');
    const joinForm = el('form', 'pk-online-join');
    joinForm.append(codeInput, el('button', 'secondary', '加入房间'));
    joinForm.onsubmit = e => {
      e.preventDefault();
      const code = codeInput.value.trim();
      if (!/^\d{6}$/.test(code)) return void (panelError.textContent = '房间码是6位数字。');
      join(code);
    };
    const classBox = el('div', 'pk-online-class');
    onlinePanel.append(createButton, el('p', 'pk-online-or', '或者加入同学的房间'), joinForm, classBox, panelError);
    async function loadClassRooms() {
      let data;
      try {
        data = await scorePost(ROOM.lobby);
      } catch {
        return;
      }
      classBox.replaceChildren();
      if (!data.rooms.length) return;
      classBox.append(el('h3', '', data.rooms.some(r => !r.mine) ? '班级里正在等人的房间' : '可以加入的房间'));
      for (const r of data.rooms) {
        const row = el('div', 'pk-online-room');
        const who = el('span', 'pk-online-who');
        who.append(el('strong', '', r.mine ? '我的另一台设备开的房间' : r.host.name + ' 的房间'), el('small', '', `${r.players}/${r.max} 人`));
        row.append(avatar(r.host), who, button('加入', () => join(r.code), 'primary'));
        classBox.append(row);
      }
    }
    function showPanel() {
      panelError.textContent = '';
      clearInterval(o.panelTimer);
      if (!bank) return;
      loadClassRooms();
      o.panelTimer = setInterval(() => !document.hidden && !startScreen.hidden && loadClassRooms(), 5000);
    }
    function hidePanel() {
      clearInterval(o.panelTimer);
    }

    // ---- lobby ----
    function renderLobby() {
      const st = o.state;
      lobbyScreen.replaceChildren();
      const top = el('div', 'pk-top');
      const back = button('← 离开房间', () => leave(), 'pk-home');
      top.append(back, tools);
      const box = el('main', 'pk-card');
      box.append(el('p', 'pk-eyebrow', `联网PK · ${cfg.title}`));
      box.append(el('p', 'pk-lobby-label', '房间码'), el('p', 'pk-lobby-code', st.code));
      const invite = button('复制房间码', async () => {
        if (await copyText(st.code)) {
          toast(`已复制房间码 ${st.code}，发给同学吧`);
          invite.textContent = '已复制 ✓';
        } else toast('复制失败，请长按房间码手动复制', false);
      }, st.practice ? 'pk-invite-main' : 'secondary');
      box.append(invite, el('p', 'pk-help', '同学在这个游戏里选「联网PK」，输入房间码就能加入；同班同学还能直接在列表里点「加入」。'));
      const grid = el('ul', 'pk-lobby-players');
      for (const p of st.players) {
        const li = el('li', p.seat === st.me ? 'me' : '');
        li.append(avatar(p), el('span', '', p.name + (p.host ? ' · 房主' : '')));
        grid.append(li);
      }
      box.append(el('h2', 'pk-lobby-count', `已加入 ${st.players.length}/${st.max} 人`), grid);
      // One account on several devices: still playable, but nudge towards a classmate.
      if (st.practice && st.status === 'lobby') {
        box.append(el('p', 'pk-practice-note', st.players.length > 1
          ? '现在都是你自己的设备，这局是自家练习赛，不计入班级排行。把房间码发给同学，一起比才能为排行加分 🏆'
          : '把房间码发给同学，等同学加入就能开始PK；用同一个授权码的另一台设备加入，可以先来一局自家练习赛（不计分）。'));
      }
      const status = el('p', 'pk-lobby-status');
      status.setAttribute('role', 'status');
      if (st.status === 'running') {
        const left = Math.max(1, Math.ceil((st.startsAt * 1000 - serverNow()) / 1000));
        status.textContent = `${left} 秒后开始！`;
        status.classList.add('go');
        box.append(status);
      } else if (st.host === st.me) {
        const enough = st.players.length >= 2;
        const label = !enough ? '等同学加入…（至少2人）' : st.practice ? '开始练习赛（不计分）' : `开始PK（${st.players.length}人）`;
        const start = button(label, () => begin(start), 'pk-primary');
        start.disabled = !enough;
        box.append(start, el('p', 'pk-help', `本局 ${st.seconds} 秒；可以在右上角「设置」里改时长，再重新创建房间。`));
      } else {
        status.textContent = '等房主开始…';
        box.append(status);
      }
      const error = el('p', 'pk-error', o.notice || '');
      error.setAttribute('role', 'alert');
      box.append(error);
      lobbyScreen.append(top, box);
    }
    function showLobby() {
      startScreen.hidden = true;
      gameScreen.hidden = true;
      resultScreen.hidden = true;
      lobbyScreen.hidden = false;
      renderLobby();
    }

    // ---- live scores during the round and the final ranking ----
    function renderBar() {
      const st = o.state;
      onlineBar.replaceChildren();
      const sorted = [...st.players].sort((a, b) => b.score - a.score);
      for (const p of sorted) {
        const chip = el('div', 'pk-online-chip' + (p.seat === st.me ? ' me' : ''));
        const name = el('span', 'pk-online-name', p.name);
        chip.append(avatar(p), name, el('strong', '', String(p.score)));
        chip.title = `${p.name}：${p.score} 分，已答 ${p.answered} 题`;
        onlineBar.append(chip);
      }
      if (o.notice) onlineBar.append(el('p', 'pk-online-notice', o.notice));
    }
    const resultsHolder = el('div', 'pk-online-results');
    function renderResults() {
      const st = o.state;
      resultsHolder.replaceChildren();
      if (!st) return;
      const ended = st.status === 'ended';
      const mine = st.players.find(p => p.seat === st.me);
      if (ended && mine) {
        const head = resultScreen.querySelector('h2');
        if (head) head.textContent = mine.rank === 1 && st.players.length > 1 ? '你是第一名！🏆' : `你是第 ${mine.rank} 名！`;
      }
      const list = el('ol', 'pk-online-rank');
      for (const p of st.players) {
        const li = el('li', p.seat === st.me ? 'me' : '');
        const who = el('span', 'pk-online-who');
        const name = el('span', 'pk-online-name-line');
        name.append(el('strong', '', p.name));
        if (p.tier) name.append(el('span', 'tier t-' + p.tier.key, p.tier.name));
        who.append(name, el('small', '', `答对 ${p.correct} 题 · 已答 ${p.answered} 题`));
        const pts = el('span', 'pk-online-points', `${p.score} 分`);
        if (ended && p.points) pts.append(el('small', '', `排行 +${p.points - p.bonus}` + (p.bonus ? ` +${p.bonus} 名次奖励` : '')));
        else if (ended && p.bonus) pts.append(el('small', '', ` +${p.bonus} 名次奖励`));
        li.append(el('span', 'pk-online-rankno', ended ? String(p.rank) : '·'), avatar(p), who, pts);
        list.append(li);
      }
      resultsHolder.append(list);
      if (!ended) return void resultsHolder.append(el('p', 'pk-help', '等其他同学答完或时间到，就公布最终名次…'));
      if (st.practice) {
        const note = el('p', 'pk-practice-note', '这是自家设备之间的练习赛，不计分。下次邀请同学来PK，前三名都有名次奖励（第一名最多 +20）！');
        if (!st.inClass) {
          const link = el('a', '', '去加入班级');
          link.href = ROOM.me;
          note.append(' ', link);
        }
        resultsHolder.append(note);
      } else if (mine && !mine.counted) {
        resultsHolder.append(el('p', 'pk-help', '同一个账号在这局有好几台设备，只按得分最高的一台计入班级排行。'));
      } else {
        resultsHolder.append(el('p', 'pk-help', '本局得分的十分之一加名次奖励已计入排行和段位：两人局第一名 +10，三人以上前三名 +20/+10/+5，其他同学不加也不扣（每人每天最多30局联网PK计分）。'));
        if (mine && mine.tier && mine.points) {
          const t = mine.tier;
          resultsHolder.append(el('p', 'pk-rank-note', `我的段位：${t.name} ` + (t.slots ? '★'.repeat(t.stars) + '☆'.repeat(t.slots - t.stars) : '★×' + t.stars)));
        }
      }
    }
    function resultsBox() {
      renderResults();
      return resultsHolder;
    }

    // ---- talking to the server ----
    // fresh: st just came from the server (its `now` sets the clock offset); timers re-apply the stored state.
    function apply(st, fresh = true) {
      const was = o.state;
      o.state = st;
      if (fresh) o.offset = st.now * 1000 - Date.now();
      if (st.status === 'closed' || !st.joined) {
        stop();
        const reason = st.status === 'closed' ? '房主已经关闭了这个房间。' : '你已经离开了这个房间（很久没有响应）。';
        backToStart(reason);
        return;
      }
      if (st.status === 'lobby') return void showLobby();
      if (st.status === 'running' && !o.begun) {
        if (serverNow() < st.startsAt * 1000) {
          showLobby();
          clearTimeout(o.beginTimer);
          o.beginTimer = setTimeout(() => o.state && !o.begun && apply(o.state, false), Math.min(1000, st.startsAt * 1000 - serverNow()));
          return;
        }
        return void play(st);
      }
      if (st.status === 'running' || st.status === 'ended') {
        if (!o.begun) return void play(st);
        renderBar();
        if (st.status === 'ended') {
          if (round.running) finish();
          else renderResults();
          if (!was || was.status !== 'ended') stop();
        }
      }
    }
    function play(st) {
      const questions = st.questions.map(question);
      const retry = 'pk-room-reload-' + st.code;
      if (questions.some(q => !q)) {
        // This page's bank is older than the room's questions: reload once (same seat, ?room= rejoins) to fetch it.
        let reloaded = false;
        try {
          reloaded = sessionStorage.getItem(retry) === '1';
          if (!reloaded) sessionStorage.setItem(retry, '1');
        } catch {}
        if (!reloaded) return void location.reload();
        o.notice = '题目没能加载，请返回首页重新进入这个游戏。';
        return void showLobby();
      }
      try {
        sessionStorage.removeItem(retry);
      } catch {}
      o.begun = true;
      round.mode = 'online';
      startRound({ draws: [questions], deadline: st.deadline * 1000 - o.offset });
      // Answers already sent before a reload count on the server; continue from there.
      const s = round.players[0];
      const mine = st.players.find(p => p.seat === st.me);
      if (mine && mine.answered) {
        s.index = Math.min(mine.answered, questions.length);
        s.score = mine.score;
        s.correct = mine.correct;
        s.marks = Array(mine.answered).fill(true);
        load(0);
      }
      renderBar();
      if (st.status === 'ended') finish();
    }
    function schedule(delay = POLL) {
      clearTimeout(o.timer);
      if (o.code) o.timer = setTimeout(poll, delay);
    }
    async function poll() {
      if (!o.code) return;
      if (document.hidden && !round.running) return schedule(POLL * 2);
      try {
        const data = await scorePost(roomUrl('state') + '?v=' + (o.state ? o.state.version : 0));
        if (o.fails) o.notice = '';
        o.fails = 0;
        if (data.same) o.offset = data.now * 1000 - Date.now();
        else apply(data);
      } catch (e) {
        o.fails++;
        if (e.status === 404 || e.status === 403) {
          stop();
          return void backToStart(e.status === 404 ? '这个房间已经不存在了。' : e.message);
        }
        o.notice = '网络不稳定，正在重连…';
        if (!lobbyScreen.hidden) renderLobby();
        else if (round.running) renderBar();
      }
      schedule(o.fails ? Math.min(10000, POLL * 2 ** o.fails) : POLL);
    }
    function stop() {
      clearTimeout(o.timer);
      clearTimeout(o.beginTimer);
      o.timer = 0;
    }
    async function enter(promise) {
      panelError.textContent = '';
      audio.unlock();
      try {
        const st = await promise;
        if (st.game !== cfg.id) {
          // The code belongs to another game: open that game's page, which joins the same room (same seat).
          panelError.textContent = `这个房间玩的是「${st.title}」，正在为你切换…`;
          location.href = new URL(st.link, location.href).href;
          return;
        }
        stop();
        o.code = st.code;
        o.state = null;
        o.begun = false;
        o.notice = '';
        o.queue = Promise.resolve();
        setUrl(st.code);
        hidePanel();
        apply(st);
        schedule();
      } catch (e) {
        panelError.textContent = e.message;
        if (!startScreen.hidden) return;
        backToStart(e.message);
      }
    }
    function create() {
      if (!bank) return;
      duration = validSeconds(duration) ? duration : cfg.defaultSeconds;
      if (o.code && o.state?.status === 'lobby') scorePost(roomUrl('leave'), {}).catch(() => {});
      enter(scorePost(ROOM.create, { seconds: duration }));
    }
    function join(code) {
      if (!bank) return;
      enter(scorePost(ROOM.join.replace('CODE', code), {}));
    }
    async function begin(startButton) {
      startButton.disabled = true;
      let draws;
      try {
        draws = drawQuestions(1);
      } catch (e) {
        o.notice = e.message;
        return void renderLobby();
      }
      try {
        apply(await scorePost(roomUrl('start'), { questions: draws[0].map(q => q.id) }));
      } catch (e) {
        o.notice = e.message;
        renderLobby();
      }
    }
    // Answers go out one at a time, in order and at least a second apart (the server refuses faster ones).
    function report(i, correct) {
      o.queue = o.queue.then(async () => {
        for (let attempt = 0; attempt < 4; attempt++) {
          const wait = o.lastPost + 1100 - Date.now();
          if (wait > 0) await new Promise(r => setTimeout(r, wait));
          o.lastPost = Date.now();
          try {
            await scorePost(roomUrl('answer'), { index: i, correct });
            return;
          } catch (e) {
            if (e.status === 409) return; // already counted, or the round is over
            if (e.status !== 0 && e.status !== 429 && e.status !== 503) return;
            await new Promise(r => setTimeout(r, 1000 * (attempt + 1)));
          }
        }
      });
      // Show my new score right away; the next poll brings everybody else's.
      const mine = o.state?.players.find(p => p.seat === o.state.me);
      if (mine) {
        mine.score = round.players[0].score;
        mine.answered = i + 1;
        renderBar();
      }
    }
    function backToStart(message) {
      stop();
      setUrl('');
      if (round.running) {
        round.running = false;
        clearInterval(round.timer);
        audio.stop();
        document.body.classList.remove('pk-playing');
      }
      o.code = '';
      o.state = null;
      o.begun = false;
      lobbyScreen.hidden = true;
      gameScreen.hidden = true;
      resultScreen.hidden = true;
      onlineBar.hidden = true;
      startScreen.hidden = false;
      top.append(tools);
      selectMode('online');
      panelError.textContent = message || '';
      window.scrollTo(0, 0);
    }
    function leave() {
      if (o.code && o.state?.status === 'lobby') scorePost(roomUrl('leave'), {}).catch(() => {});
      backToStart('');
    }
    // Closing the tab while waiting frees the seat (the server also drops players it has not heard from in 45 s).
    window.addEventListener('pagehide', () => {
      if (!o.code || o.state?.status !== 'lobby' || !csrfValue || !navigator.sendBeacon) return;
      const form = new FormData();
      form.append('csrf', csrfValue);
      navigator.sendBeacon(roomUrl('leave'), form);
    });
    document.addEventListener('visibilitychange', () => !document.hidden && o.code && schedule(0));
    return { showPanel, hidePanel, create, join, leave, report, resultsBox, get code() { return o.code; } };
  })();

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
    showUnits();
    go.disabled = false;
    const invited = new URLSearchParams(location.search).get('room');
    if (ROOM && /^\d{6}$/.test(invited || '')) {
      selectMode('online');
      online.join(invited);
    } else selectMode(location.hash === '#mistakes' && mistakes.ids().length ? 'mistakes' : 'pk');
  }
  updateSettingsLabels();
  selectMode('pk');
  window.PK = {
    register(type, r) {
      renderers[type] = r;
    },
    renderer: type => renderers[type],
    el, button, shuffle, svg,
    figures: {},
    url,
    // Plays a recording from the site's static/audio/ folder (english listening questions).
    say: name => audio.speak(url('static/audio/' + name)),
    page: PAGE,
    // Silences music, effects and recordings while the microphone records (读句子); quiet(false) brings music back.
    quiet: on => (on ? audio.stop() : audio.resume()),
    // test hooks
    _round: round, _submit: submit, _next: next, _current: current,
  };
  document.addEventListener('DOMContentLoaded', boot);
})();
