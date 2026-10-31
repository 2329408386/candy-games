(()=>{'use strict';
const cfg=JSON.parse(document.getElementById('experience-config').textContent),key='math-pk-settings-v1';let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')||{};}catch{}if(typeof saved!=='object'||Array.isArray(saved))saved={};
let musicOn=saved.music!==false,fxOn=saved.fx!==false,voiceOn=saved.voice!==false,duration=cfg.defaultSeconds,deadline=0,running=false,unlocked=false,ending=false,lastClick=0,lastVoice=-Infinity;const effects={};const feedbacks=new Map();let voice=null;let volumeFrame=0;
const valid=n=>Number.isInteger(n)&&n>=30&&n<=1800;
if(valid(saved.durations?.[cfg.id]))duration=saved.durations[cfg.id];
const bg=new Audio();bg.loop=true;bg.preload='none';bg.volume=.45;
function persist(){try{const fresh=JSON.parse(localStorage.getItem(key)||'{}');saved=fresh&&typeof fresh==='object'&&!Array.isArray(fresh)?fresh:{};}catch{}saved.music=musicOn;saved.fx=fxOn;saved.voice=voiceOn;if(!saved.durations||typeof saved.durations!=='object'||Array.isArray(saved.durations))saved.durations={};saved.durations[cfg.id]=duration;try{localStorage.setItem(key,JSON.stringify(saved));}catch{}}
const audioRoot=new URL('../static/game-audio-v3/',document.baseURI).href;
const audioFiles=Object.freeze({background:'GameSceneBGM.901e1914.mp3',click:'effect-6.f04fbe20.mp3',success:'effect-3.ab6b19f8.mp3','success-combo':'effect-5.f2efcf54.mp3','success-streak':'effect-8.c1e4c173.mp3',failure:'effect-1.7f41f014.mp3',cheer:'effect-2.fc93a259.mp3'});
function duck(){
 const target=voice&&!voice.paused?.06:feedbacks.size?.12:.45;
 cancelAnimationFrame(volumeFrame);
 function step(){bg.volume+= (target-bg.volume)*.25;if(Math.abs(target-bg.volume)>.002)volumeFrame=requestAnimationFrame(step);else bg.volume=target;}
 volumeFrame=requestAnimationFrame(step);
}
function haltResults(){if(effects.click)effects.click.pause();for(const a of feedbacks.values())a.pause();feedbacks.clear();if(voice)voice.pause();voice=null;duck();}
function play(name,player='click'){
 if(!unlocked||!fxOn||document.hidden)return;
 try{
  const key=name==='click'?'click':'feedback-'+player;
  const previous=feedbacks.get(key);if(previous){previous.pause();feedbacks.delete(key);}
  const poolKey=name==='click'?'click':name+':'+player;
  const a=effects[poolKey]||(effects[poolKey]=new Audio(audioRoot+audioFiles[name]));a.currentTime=0;a.volume=name==='click'?.7:.55;
  if(name!=='click'){feedbacks.set(key,a);a.onended=a.onerror=()=>{if(feedbacks.get(key)===a)feedbacks.delete(key);duck();};}
  a.play().then(duck).catch(()=>{if(feedbacks.get(key)===a)feedbacks.delete(key);duck();});duck();
 }catch{}
}
function encourage(){
 if(!unlocked||!fxOn||!voiceOn||!running||document.hidden||performance.now()-lastVoice<3000||(voice&&!voice.paused))return;
 lastVoice=performance.now();voice=new Audio(audioRoot+audioFiles.cheer);voice.volume=.5;const current=voice;
 current.onended=current.onerror=()=>{if(voice===current)voice=null;duck();};
 current.play().then(duck).catch(()=>{if(voice===current)voice=null;duck();});duck();
}
function resume(){if(unlocked&&musicOn&&running&&!document.hidden){if(!bg.src)bg.src=audioRoot+audioFiles.background;bg.play().catch(()=>{});}else bg.pause();}
function click(){unlocked=true;const now=performance.now();if(now-lastClick>65){lastClick=now;play('click');}resume();}
document.addEventListener('click',e=>{if(e.target.closest('button,a,input[type=checkbox],select'))click();},true);
const node=(tag,cls,text)=>{const x=document.createElement(tag);if(cls)x.className=cls;if(text!==undefined)x.textContent=text;return x;};
const start=document.getElementById('start-screen')||document.getElementById('start');start.replaceChildren();start.className='exp-start';
const top=node('div','exp-top');const home=node('a','exp-home','← 返回游戏首页');home.href='../';top.append(home);start.append(top);
const card=node('main','exp-card');const emblem=node('div','exp-emblem',cfg.emblem);emblem.setAttribute('aria-hidden','true');card.append(node('p','exp-eyebrow','三年级上册 · 数学小课堂'),emblem,node('h1','exp-title',cfg.title),node('p','exp-description',cfg.description));
const stats=node('div','exp-stats');for(const [a,b] of [['100','题库题量'],['20','每人题数'],['','本局秒数']]){const x=node('div');const strong=node('strong','',a);if(!a)strong.id='exp-duration-label';x.append(strong,node('span','',b));stats.append(x);}card.append(stats);
const rules=node('div','exp-rules');rules.append(node('h2','','怎么玩'),node('p','',cfg.instructions),node('p','exp-scoring',cfg.scoring));card.append(rules);const go=node('button','exp-primary','开始PK');go.id=cfg.shared?'start-button':'exp-start-button';go.type='button';go.onclick=()=>{if(running)return;unlocked=true;window.startGame();};card.append(go,node('p','exp-note','两位玩家分别作答 · 一起练习，共同进步'));start.append(card);
const settingsButton=node('button','exp-settings-toggle','⚙ 设置');settingsButton.type='button';settingsButton.setAttribute('aria-haspopup','dialog');document.body.append(settingsButton);
const dialog=node('dialog','exp-dialog');dialog.setAttribute('aria-label','游戏设置');dialog.innerHTML='<div class="exp-dialog-head"><h2>游戏设置</h2><button type="button" class="exp-close" aria-label="关闭设置">×</button></div><label class="exp-label" for="exp-seconds">本局时长（秒）</label><input id="exp-seconds" type="number" min="30" max="1800" step="1" inputmode="numeric"><div class="exp-presets"></div><p class="exp-help">30—1800秒；游戏开始后，本局时长固定。</p><label class="exp-switch"><span>背景音乐</span><input id="exp-music" type="checkbox"></label><label class="exp-switch"><span>点击与答题音效</span><input id="exp-fx" type="checkbox"></label><label class="exp-switch"><span>连击欢呼</span><input id="exp-voice" type="checkbox"></label><p class="exp-help">连续答对5题及以上时播放欢呼；关闭点击与答题音效时，欢呼也会静音。</p><p class="exp-error" role="status"></p><div class="exp-actions"><button type="button" class="exp-reset">恢复默认</button><button type="button" class="exp-save">保存设置</button></div>';
document.body.append(dialog);const input=dialog.querySelector('#exp-seconds'),music=dialog.querySelector('#exp-music'),fx=dialog.querySelector('#exp-fx'),voiceToggle=dialog.querySelector('#exp-voice'),error=dialog.querySelector('.exp-error');for(const seconds of [60,120,150,180,300,600]){const b=node('button','exp-preset',seconds+'秒');b.type='button';b.onclick=()=>{input.value=seconds;error.textContent='';};dialog.querySelector('.exp-presets').append(b);}
function update(){document.getElementById('exp-duration-label').textContent=duration;settingsButton.textContent=musicOn||fxOn?'⚙ 设置':'⚙ 设置 · 已静音';}
settingsButton.onclick=()=>{input.value=duration;input.disabled=running;dialog.querySelectorAll('.exp-preset,.exp-reset').forEach(b=>b.disabled=running);music.checked=musicOn;fx.checked=fxOn;voiceToggle.checked=voiceOn;error.textContent='';dialog.showModal();};
dialog.querySelector('.exp-close').onclick=()=>dialog.close();dialog.querySelector('.exp-reset').onclick=()=>{input.value=cfg.defaultSeconds;};dialog.querySelector('.exp-save').onclick=()=>{const n=Number(input.value);if(!running&&!valid(n)){error.textContent='请输入30至1800之间的整数秒数。';input.focus();return;}if(!running)duration=n;musicOn=music.checked;fxOn=fx.checked;voiceOn=voiceToggle.checked;if(!fxOn)haltResults();else if(!voiceOn&&voice){voice.pause();voice=null;duck();}persist();update();resume();dialog.close();};
function endRound(){running=false;ending=false;bg.pause();haltResults();if(effects.click)effects.click.pause();document.body.classList.remove('exp-playing');document.body.append(settingsButton);}
function expired(){if(!running)return false;if(Date.now()<deadline)return false;if(!ending){ending=true;cfg.shared?window.finish():window.endGame();}return true;}
window.GameExperience={getDuration:()=>duration,startRound(){duration=valid(duration)?duration:cfg.defaultSeconds;deadline=Date.now()+duration*1000;running=true;ending=false;lastVoice=-Infinity;document.body.classList.add('exp-playing');const bar=document.querySelector(cfg.shared?'header.top':'#game-screen .header');if(bar)bar.append(settingsButton);resume();return deadline;},endRound,result(correct,options={}){if(!running)return;const combo=Number.isInteger(options.combo)?options.combo:1;play(correct?(combo>=5?'success-streak':combo>=2?'success-combo':'success'):'failure',options.player??0);if(correct&&combo>=5)encourage();},remaining(){return Math.max(0,Math.ceil((deadline-Date.now())/1000));},expired,active:()=>running};
document.addEventListener('visibilitychange',()=>{if(document.hidden){bg.pause();haltResults();if(effects.click)effects.click.pause();}else{expired();resume();}});window.addEventListener('pageshow',()=>{expired();resume();});window.addEventListener('pagehide',()=>{bg.pause();haltResults();if(effects.click)effects.click.pause();});window.addEventListener('storage',e=>{if(e.key!==key)return;try{const s=JSON.parse(e.newValue||'{}');musicOn=s.music!==false;fxOn=s.fx!==false;voiceOn=s.voice!==false;if(!fxOn)haltResults();else if(!voiceOn&&voice){voice.pause();voice=null;duck();}if(!running&&valid(s.durations?.[cfg.id]))duration=s.durations[cfg.id];update();resume();}catch{}});update();
})();
