(()=>{'use strict';
const config=JSON.parse(document.getElementById('math-config').textContent),M=MathCommon;
const state={players:[],started:false,ended:true,timer:null};
const $=id=>document.getElementById(id);
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
const button=(text,fn,cls='card')=>{const b=el('button',cls,text);b.type='button';b.onclick=fn;return b;};
function current(p){const s=state.players[p];return s?.questions[s.index];}
function editable(p){if(!state.started||state.ended||GameExperience.expired())return false;return !!current(p)&&!state.players[p].locked;}
function startGame(){
 if(GameExperience.active())return;
 if(config.id==='measure'&&(!window.MathMeasureObjects||!M.banks.measure.every(q=>MathMeasureObjects.catalog[q.objectType]))){alert('物品图片暂未加载，请刷新重试。');return;}
 clearInterval(state.timer);const rounds=M.roundFor(config.id);state.started=true;state.ended=false;
 state.players=rounds.map(questions=>({questions,index:0,score:0,combo:0,correct:0,locked:false,choice:null,parts:[],activePart:0,feedback:null}));
 $('start-screen').hidden=true;$('game-screen').hidden=false;$('end-screen').hidden=true;
 GameExperience.startRound();[0,1].forEach(loadQuestion);tick();state.timer=setInterval(tick,200);
}
function endGame(){
 if(state.ended)return;state.ended=true;clearInterval(state.timer);GameExperience.endRound();
 [0,1].forEach(render);$('end-screen').hidden=false;
 const [a,b]=state.players;$('winner-text').textContent=a.score===b.score?'平局！':a.score>b.score?'玩家一获胜！':'玩家二获胜！';
 $('final-score').textContent=`玩家一 ${a.score} 分 · 答对 ${a.correct} 题　｜　玩家二 ${b.score} 分 · 答对 ${b.correct} 题`;
 if(GameExperience.remaining()===0)$('timer').textContent='0';
 $('end-screen').scrollIntoView({behavior:'smooth',block:'nearest'});
}
function tick(){if(state.ended)return;$('timer').textContent=GameExperience.remaining();GameExperience.expired();}
function loadQuestion(p){
 const s=state.players[p];s.locked=false;s.choice=null;s.parts=(current(p)?.answerUnits||[]).map(()=> '');s.activePart=0;s.feedback=null;render(p);
 if(state.players.every(x=>x.index>=x.questions.length))endGame();
}
function submitAnswer(p){
 if(!editable(p))return;const s=state.players[p],q=current(p);
 if(q.kind==='measure'?(s.parts.some(v=>v==='')):s.choice===null){$('notice-'+p).textContent=q.kind==='measure'?'请填写全部数字框，再提交。':'请先选择答案，再提交。';return;}
 const value=q.kind==='measure'?s.parts:s.choice,correct=M.checkAnswer(q,value);s.locked=true;
 if(correct){s.combo++;s.correct++;s.score+=10+(s.combo>1?5:0);}else{s.combo=0;s.score=Math.max(0,s.score-5);}
 s.feedback={correct};GameExperience.result(correct,{player:p,combo:s.combo});render(p);
}
function nextQuestion(p){
 if(!state.started||state.ended||GameExperience.expired())return;const s=state.players[p];
 if(!s.locked||!current(p))return;s.index++;loadQuestion(p);
}
function svgNode(html,cls){const wrap=el('div',cls);wrap.innerHTML=html;return wrap;}
function ruler(q,p){
 const width=640,x0=30,w=580,top=40,X=mm=>x0+mm/q.rulerLengthMm*w;
 const object=MathMeasureObjects.draw(q,{xStart:X(q.startMm),xEnd:X(q.endMm),top,prefix:'player-'+p});
 const objectBottom=top+object.height,y=objectBottom+28,height=y+65;
 let ticks='';
 for(let mm=0;mm<=q.rulerLengthMm;mm+=q.minorEveryMm){
  const major=mm%q.labelEveryMm===0,x=X(mm);ticks+=`<path d="M${x} ${y}v${major?24:12}" stroke="#62745e" stroke-width="${major?1.6:.7}"/>`;
  if(major)ticks+=`<text x="${x}" y="${y+45}" text-anchor="middle" font-size="12">${mm/M.FACTORS[q.rulerLabelUnit]}</text>`;
 }
 return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${q.object}的实物插画及起点终点"><text x="320" y="22" text-anchor="middle" font-size="14">${q.object} · 尺面标数单位：${q.rulerLabelUnit}</text><path data-start-guide d="M${X(q.startMm)} ${top-4}V${y}" stroke="#ad7632" stroke-dasharray="3 3"/><path data-end-guide d="M${X(q.endMm)} ${top-4}V${y}" stroke="#ad7632" stroke-dasharray="3 3"/>${object.svg}<text x="${Math.max(20,X(q.startMm))}" y="${objectBottom+18}" text-anchor="middle" font-size="11">起点</text><text x="${Math.min(620,X(q.endMm))}" y="${objectBottom+18}" text-anchor="middle" font-size="11">终点</text><rect x="${x0-8}" y="${y}" width="${w+16}" height="53" rx="8" fill="#f4e6be"/>${ticks}</svg>`;
}
function zoomRuler(q,point,label){
 const from=Math.max(0,Math.floor(point/10)*10-10),to=from+30,w=280,x0=15,X=mm=>x0+(mm-from)/30*w;
 let ticks='';
 for(let mm=from;mm<=to;mm++){
  const major=mm%10===0;ticks+=`<path d="M${X(mm)} 46v${major?22:12}" stroke="#62745e"/>`;
  if(major)ticks+=`<text x="${X(mm)}" y="85" text-anchor="middle" font-size="13">${mm/10}</text>`;
 }
 return `<svg viewBox="0 0 310 102" role="img" aria-label="${label}局部放大，每小格1毫米"><text x="155" y="18" text-anchor="middle" font-size="12">${label}放大 · 标数单位：厘米</text><rect x="7" y="46" width="296" height="45" rx="4" fill="#f4e6be"/>${ticks}<path d="M${X(point)} 25v43" stroke="#ae672e" stroke-width="2"/><circle cx="${X(point)}" cy="35" r="3" fill="#ae672e"/></svg>`;
}
function gridMap(q){
 const positions=q.landmarks.map(a=>({...a,px:65+a.x*105,py:60+a.y*105}));
 return `<svg viewBox="0 0 340 325" role="img" aria-label="校园示意图${q.mapId}，上北下南左西右东"><text x="170" y="18" text-anchor="middle" font-size="13">北 ↑</text>`+positions.map(a=>{
  const ref=a.name===q.ref.name,target=a.name===q.target.name;
  return `<rect x="${a.px-44}" y="${a.py-22}" width="88" height="44" rx="10" fill="${ref?'#e5f0f7':target?'#fff0c9':'#f2f5eb'}" stroke="${ref?'#6f95b0':target?'#bb964b':'#d6dfcd'}" stroke-width="${ref||target?2.5:1}"/><text x="${a.px}" y="${a.py+5}" text-anchor="middle" font-size="14">${a.name}</text>`;
 }).join('')+'</svg>';
}
function clockMap(q,p){
 const cx=190,cy=190,position=(clock,r)=>({x:cx+Math.sin(clock*Math.PI/6)*r,y:cy-Math.cos(clock*Math.PI/6)*r});
 const coords=a=>a.center?{x:cx,y:cy}:position(a.clock,112);
 let items=q.landmarks.map(a=>{
  const pos=coords(a),ref=a.name===q.ref.name,target=a.name===q.target.name;
  return `<rect x="${pos.x-25}" y="${pos.y-12}" width="50" height="24" rx="6" fill="${ref?'#e5f0f7':target?'#fff0c9':'#f6f7f1'}" stroke="${ref?'#6f95b0':target?'#bb964b':'#d8e1d0'}"/><text x="${pos.x}" y="${pos.y+5}" font-size="14" text-anchor="middle">${a.name}</text>`;
 }).join('');
 const ref=coords(q.ref),head=position(q.heading,40),hx=ref.x+head.x-cx,hy=ref.y+head.y-cy;
 return `<svg viewBox="0 0 380 380" role="img" aria-label="校园示意图，观察者面向正${q.headingLabel}"><defs><marker id="face-arrow-${p}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0 0 10 5 0 10z" fill="#6f95b0"/></marker></defs><circle cx="190" cy="190" r="160" fill="#f6f7ef" stroke="#dce4d4"/><text x="190" y="20" text-anchor="middle" font-size="13">北 ↑</text><text x="367" y="195" text-anchor="middle" font-size="12">东</text><text x="190" y="374" text-anchor="middle" font-size="12">南</text><text x="12" y="195" text-anchor="middle" font-size="12">西</text>${items}<circle cx="190" cy="190" r="27" fill="${q.ref.center?'#e5f0f7':'#fff0c9'}" stroke="#b7c9b1"/><text x="190" y="195" font-size="13" text-anchor="middle">操场</text><path d="M${ref.x} ${ref.y}L${hx} ${hy}" stroke="#6f95b0" stroke-width="2.5" marker-end="url(#face-arrow-${p})"/></svg>`;
}
function renderMeasure(area,p,q,s){
 area.append(el('p','measure-hint',q.variant==='offset'?'起点不是0：用终点刻度减去起点刻度。':'起点对准0：读出物体右端的刻度。'),svgNode(ruler(q,p),'measure-visual'));
 if(q.answerUnits.includes('毫米')){const zooms=el('div','ruler-zooms');zooms.append(svgNode(zoomRuler(q,q.startMm,'起点'),'ruler-zoom'),svgNode(zoomRuler(q,q.endMm,'终点'),'ruler-zoom'));area.append(zooms,el('p','muted','放大图每小格1毫米，与上方尺面是同一刻度。'));}
 const fields=el('div','answer-fields');
 const inputs=[];
 q.answerUnits.forEach((unit,index)=>{
  const label=el('label','answer-part'),input=el('input');input.type='text';input.inputMode='numeric';input.pattern='[0-9]*';input.maxLength=5;input.value=s.parts[index];input.disabled=s.locked||state.ended;input.setAttribute('aria-label',`玩家${p+1} ${unit}数`);
  input.onfocus=()=>{s.activePart=index;inputs.forEach((n,i)=>n.classList.toggle('active',i===index));};
  input.oninput=()=>{if(!editable(p))return;input.value=input.value.replace(/\D/g,'').slice(0,5);s.parts[index]=input.value;};inputs.push(input);label.append(input,el('span','',unit));fields.append(label);
 });
 inputs[s.activePart]?.classList.add('active');area.append(fields);
 const pad=el('div','keypad');
 for(const key of ['1','2','3','4','5','6','7','8','9','清空','0','⌫']){
  const b=button(key,()=>{
   if(!editable(p))return;const i=s.activePart;let value=s.parts[i];
   if(key==='清空')value='';else if(key==='⌫')value=value.slice(0,-1);else value=(value+key).slice(0,5);
   s.parts[i]=value;inputs[i].value=value;inputs[i].focus();
  },'key');b.disabled=s.locked||state.ended;pad.append(b);
 }
 area.append(pad);
}
function render(p){
 const s=state.players[p],q=current(p),area=$('body-'+p);area.replaceChildren();$('score-'+p).textContent=s.score;
 if(!q){area.append(el('p','waiting','本轮20题已完成，等待另一位玩家。'));return;}
 area.append(el('p','source',`第 ${s.index+1} / 20 题 · ${q.source.unit} · ${q.source.lesson}`),el('h2','prompt',q.prompt));
 if(q.kind==='measure')renderMeasure(area,p,q,s);
 else{
  if(q.kind==='direction')area.append(svgNode(q.category==='eight'?gridMap(q):clockMap(q,p),'map-wrap'),el('p','map-legend',q.category==='eight'?'蓝框是参照地点，黄框是目标地点；上北下南，左西右东。':`蓝色箭头指向观察者正前方（正${q.headingLabel}），这才是你的12点钟方向。`));
  const tray=el('div','options'+(q.kind==='direction'?' direction-options':''));
  for(const value of q.options){const b=button(String(value)+(q.category==='clock'?'点钟':''),()=>{if(!editable(p))return;s.choice=value;render(p);},'card'+(String(s.choice)===String(value)?' selected':''));b.setAttribute('aria-pressed',String(String(s.choice)===String(value)));b.disabled=s.locked||state.ended;tray.append(b);}area.append(tray);
 }
 const notice=el('p','notice');notice.id='notice-'+p;notice.setAttribute('role','status');area.append(notice);
 if(s.feedback){
  const feedback=el('div','feedback '+(s.feedback.correct?'correct':'wrong'));feedback.setAttribute('role','status');
  feedback.append(el('strong','',s.feedback.correct?(s.combo>1?'答对了！+15分':'答对了！+10分'):'再想一想，看看解析'),el('p','','正确答案：'+q.answer+(q.kind==='direction'?(q.category==='clock'?'点钟方向':'方向'):q.toUnit||'')),el('p','',q.explanation));
  const details=el('details');details.append(el('summary','','查看知识点来源'),el('p','',q.source.book+' · '+q.source.lesson+' · '+q.source.point+'（'+q.source.type+'）'));feedback.append(details);area.append(feedback);
 }
 if(!s.locked&&!state.ended)area.append(button('提交答案',()=>submitAnswer(p),'primary'));
 if(s.locked&&!state.ended)area.append(button(s.index===19?'完成本轮':'下一题',()=>nextQuestion(p),'primary next'));
}
window.startGame=startGame;window.endGame=endGame;
window.MathGame={state,current,startGame,endGame,submitAnswer,nextQuestion,render};
$('restart').onclick=startGame;
})();
