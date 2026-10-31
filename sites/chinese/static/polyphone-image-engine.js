'use strict';
const config=JSON.parse(document.getElementById('game-config').textContent);
const questionBank=JSON.parse(document.getElementById('question-bank').textContent);
const state={started:false,ended:false,deadline:0,timer:null,players:[]};
const $=id=>document.getElementById(id);
function shuffle(xs){const a=[...xs];for(let i=a.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]];}return a;}
function element(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
function button(text,fn,cls='card'){const b=element('button',cls,text);b.type='button';b.onclick=fn;return b;}
function startGame(){clearInterval(state.timer);state.started=true;state.ended=false;state.deadline=GameExperience.startRound();state.players=[0,1].map(()=>({questions:shuffle(questionBank).slice(0,20),index:0,score:0,combo:0,locked:false,selection:[],deleted:[],option:null,inserted:null,insertPosition:null,history:[]}));$('start').hidden=true;$('result').hidden=true;$('arena').hidden=false;$('time').textContent=GameExperience.getDuration();[0,1].forEach(loadQuestion);state.timer=setInterval(tick,200);}
function current(p){return state.players[p].questions[state.players[p].index];}
function editable(p){if(!state.started||state.ended)return false;if(Date.now()>=state.deadline){finish();return false;}const s=state.players[p];return s&&!s.locked&&!!current(p);}
function snapshot(s){return {selection:[...s.selection],deleted:[...s.deleted],option:s.option,inserted:s.inserted,insertPosition:s.insertPosition};}
function change(p,fn){if(!editable(p))return;const s=state.players[p];s.history.push(snapshot(s));fn(s);render(p);}
function undo(p){if(!editable(p))return;const s=state.players[p];if(s.history.length)Object.assign(s,s.history.pop());render(p);}
function clearAnswer(p){change(p,s=>{s.selection=[];s.deleted=[];s.option=null;s.inserted=null;s.insertPosition=null;});}
function loadQuestion(p){const s=state.players[p];s.locked=false;s.selection=[];s.deleted=[];s.option=null;s.inserted=null;s.insertPosition=null;s.history=[];s.feedback=null;const q=current(p);if(q){s.cards=shuffle(q.cards||[]);s.options=shuffle(q.options||[]);}render(p);if(state.players.every(s=>s.index>=s.questions.length))finish();}
function selectCard(p,id){change(p,s=>{const index=s.selection.indexOf(id);if(index<0)s.selection.push(id);else s.selection.splice(index,1);});}
function toggleDelete(p,id){change(p,s=>{const index=s.deleted.indexOf(id);if(index<0)s.deleted.push(id);else s.deleted.splice(index,1);});}
function chooseOption(p,value){change(p,s=>{s.option=value;});}
function insertOption(p,position){const s=state.players[p],q=current(p);if(s.option===null||!q||q.kind!=='expand'||!Number.isInteger(position)||position<0||position>Array.from(q.baseSentence).length)return;change(p,s=>{s.inserted=s.option;s.insertPosition=position;s.option=null;});}
function answerFor(q,s){if(q.kind==='choice')return s.option;if(q.kind==='expand')return s.inserted===null?null:Array.from(q.baseSentence).slice(0,s.insertPosition).join('')+s.inserted+Array.from(q.baseSentence).slice(s.insertPosition).join('');if(q.kind==='delete')return q.cards.filter(c=>!s.deleted.includes(c.id)).map(c=>c.text).join('');return s.selection.map(id=>q.cards.find(c=>c.id===id).text).join('');}
function isCorrect(q,answer){return q.answers.includes(answer);}
function submitAnswer(p){if(!editable(p))return;const s=state.players[p],q=current(p),answer=answerFor(q,s);if(config.id!=='rhetorical'&&(answer===null||answer===''||(q.kind==='order'&&s.selection.length!==(q.requiredCount||q.cards.length)))){$('notice-'+p).textContent='请先完成答案，再提交。';return;}const correct=isCorrect(q,answer);s.locked=true;if(correct){s.combo++;s.score+=10+(s.combo>1?5:0);}else{s.combo=0;s.score=Math.max(0,s.score-5);}GameExperience.result(correct,{player:p,combo:s.combo});s.feedback={correct,answer};render(p);}
function nextQuestion(p){if(!state.started||state.ended)return;if(Date.now()>=state.deadline){finish();return;}const s=state.players[p];if(!s.locked||!current(p))return;s.index++;loadQuestion(p);}
function renderExpansion(prompt,p,q,s){
 const chars=Array.from(q.baseSentence),choosing=s.option!==null&&!s.locked&&!state.ended;
 prompt.classList.add('expand-sentence');
 for(let i=0;i<=chars.length;i++){
  if(choosing){const gap=button('+',()=>insertOption(p,i),'insertion-point');gap.dataset.position=i;gap.setAttribute('aria-label',i===0?'插入到句首':`插入到第${i}个字符后`);prompt.append(gap);}
  if(s.inserted!==null&&s.insertPosition===i)prompt.append(element('span','inserted-word',s.inserted));
  if(i<chars.length)prompt.append(document.createTextNode(chars[i]));
 }
}
function render(p){const s=state.players[p],q=current(p),area=$('body-'+p);area.replaceChildren();$('score-'+p).textContent=s.score;if(!q){area.append(element('div','waiting','本轮已完成，等待另一位玩家。'));return;}
const source=element('div','source',`第 ${s.index+1} / ${s.questions.length} 题 · ${q.source.type==='课文原句'?'《'+q.source.lesson+'》':'图片多音字资料'} · ${q.source.type}`);area.append(source,element('p','instruction',q.instruction));
const prompt=element('p','prompt');if(q.kind==='choice'){prompt.append(document.createTextNode(q.prompt.slice(0,q.focusStart)));if(q.blank){const blank=element('span','letter-blank',s.option||'');blank.setAttribute('aria-label',s.option?'已选择：'+s.option:'待填写的汉字');prompt.append(blank);}else prompt.append(element('mark','',q.prompt.slice(q.focusStart,q.focusStart+q.focusLength)));prompt.append(document.createTextNode(q.prompt.slice(q.focusStart+q.focusLength)));}else if(q.kind==='expand'){renderExpansion(prompt,p,q,s);}else prompt.textContent=q.prompt;area.append(prompt);
if(q.kind==='choice'||q.kind==='expand'){const options=element('div','options');for(const value of s.options){const b=button(value,()=>chooseOption(p,value),'card'+(s.option===value?' selected':''));b.setAttribute('aria-pressed',String(s.option===value));options.append(b);}area.append(options);}
if(q.kind==='order'){const result=element('div','answer-line');result.setAttribute('aria-label','已排列的答案');if(!s.selection.length)result.append(element('span','muted','依次点击下方词卡，组成句子'));for(const id of s.selection){const card=q.cards.find(c=>c.id===id);result.append(button(card.text,()=>selectCard(p,id),'card selected'));}const tray=element('div','options');for(const card of s.cards){const b=button(card.text,()=>selectCard(p,card.id));b.disabled=s.selection.includes(card.id);b.dataset.cardId=card.id;tray.append(b);}area.append(result,tray);}
if(q.kind==='delete'){const board=element('div','options sentence-cards');for(const card of q.cards){const deleted=s.deleted.includes(card.id);const b=button(card.text,()=>toggleDelete(p,card.id),'card'+(deleted?' deleted':''));b.setAttribute('aria-pressed',String(deleted));board.append(b);}area.append(board,element('p','preview','缩写后：'+answerFor(q,s)));}
const notice=element('p','notice','');notice.id='notice-'+p;notice.setAttribute('role','status');area.append(notice);
if(s.feedback){const feedback=element('div','feedback '+(s.feedback.correct?'correct':'wrong'));feedback.setAttribute('role','status');feedback.append(element('strong','',s.feedback.correct?(s.combo>1?'答对了！+15分':'答对了！+10分'):'再想一想，看看解析'),element('p','','答案：'+q.answers[0]),element('p','',q.explanation));const details=element('details','');details.append(element('summary','',q.source.type==='课文原句'?'查看课文原句':'查看资料来源'),element('p','',q.source.type==='课文原句'?q.source.original:((q.reference?.image||'图片资料')+' · 参考词语：'+(q.reference?.image_word||q.reference?.word||'')+'。本题为配套例句。')));feedback.append(details);area.append(feedback);}
const controls=element('div','controls');controls.append(button('撤回',()=>undo(p),'secondary'),button('清空',()=>clearAnswer(p),'secondary'),button('提交答案',()=>submitAnswer(p),'primary'));area.append(controls);
if(s.locked||state.ended)area.querySelectorAll('button').forEach(b=>b.disabled=true);
if(s.locked&&!state.ended)area.append(button(s.index===s.questions.length-1?'完成本轮':'下一题',()=>nextQuestion(p),'next'));
}
function tick(){if(state.ended)return;const seconds=Math.max(0,Math.ceil((state.deadline-Date.now())/1000));$('time').textContent=seconds;if(seconds===0)finish();}
function finish(){GameExperience.endRound();if(state.ended)return;state.ended=true;if(Date.now()>=state.deadline)$('time').textContent='0';clearInterval(state.timer);[0,1].forEach(render);$('result').hidden=false;const [a,b]=state.players.map(p=>p.score);$('result-title').textContent=a===b?'平局！':a>b?'玩家一获胜！':'玩家二获胜！';$('result-score').textContent=`玩家一 ${a} 分 · 玩家二 ${b} 分`;$('result').scrollIntoView({behavior:'smooth',block:'start'});}
$('start-button').onclick=startGame;$('restart').onclick=startGame;document.addEventListener('visibilitychange',()=>{if(state.started&&!state.ended)tick();});
