const assert=require('assert'),M=require('../static/math-common.js');
const factors={'毫米':1,'厘米':10,'分米':100,'米':1000,'千米':1000000};
const expected={conversion:{single:60,mixed:40},mental:{add2:25,sub2:25,add3:25,sub3:25},measure:{'single-mm':10,'single-cm':10,'single-dm':10,'cm-mm':30,'dm-cm':20,'dm-mm':20},units:{'毫米':20,'厘米':20,'分米':20,'米':20,'千米':20},directions:{eight:50,clock:50}};
const roundCounts={conversion:{single:12,mixed:8},mental:{add2:5,sub2:5,add3:5,sub3:5},measure:{'single-mm':2,'single-cm':2,'single-dm':2,'cm-mm':6,'dm-cm':4,'dm-mm':4},units:{'毫米':4,'厘米':4,'分米':4,'米':4,'千米':4},directions:{eight:10,clock:10}};
function count(qs){return qs.reduce((out,q)=>(out[q.category]=(out[q.category]||0)+1,out),{});}
for(const [game,bank] of Object.entries(M.banks)){
 assert.equal(bank.length,100);assert.equal(new Set(bank.map(q=>q.id)).size,100);assert.deepEqual(count(bank),expected[game]);
 const signatures=new Set();
 for(const q of bank){
  assert(q.explanation&&q.source.unit&&q.source.lesson&&q.source.point&&q.source.type==='依据教材总结自编');
  const signature=game==='measure'?JSON.stringify([q.answerUnits,q.startMm,q.endMm]):q.prompt;
  assert(!signatures.has(signature),q.id+' duplicate content');signatures.add(signature);
  if(q.kind!=='measure'){
   assert.equal(new Set(q.options.map(String)).size,q.options.length);assert(q.options.map(String).includes(String(q.answer)));assert(M.checkAnswer(q,q.answer));
   assert(!M.checkAnswer(q,q.options.find(x=>String(x)!==String(q.answer))));
  }
  if(game==='conversion'){
   const expected=q.category==='single'?q.given*factors[q.fromUnit]/factors[q.toUnit]:q.givenParts.reduce((sum,value,i)=>sum+value*factors[q.givenUnits[i]],0)/factors[q.toUnit];
   assert(Number.isInteger(expected));assert.equal(q.answer,expected,q.id);assert.equal(q.options.length,4);
  }else if(game==='mental'){
   const expected=q.operator==='+'?q.a+q.b:q.a-q.b;assert.equal(q.answer,expected);assert(expected>=0&&expected<=1000);assert.equal(q.options.length,4);
  }else if(game==='measure'){
   assert(Number.isInteger(q.startMm)&&Number.isInteger(q.endMm));assert(q.startMm>=0&&q.endMm>q.startMm&&q.endMm<=q.rulerLengthMm);
   assert.equal(q.lengthMm,q.endMm-q.startMm);assert.equal(q.startMm===0,q.variant==='zero');
   assert.equal(q.answerParts.reduce((sum,value,i)=>sum+value*factors[q.answerUnits[i]],0),q.endMm-q.startMm);
   if(q.answerUnits.length===2)assert(q.answerParts[1]>0&&q.answerParts[1]<factors[q.answerUnits[0]]/factors[q.answerUnits[1]]);
   assert(M.checkAnswer(q,q.answerParts));assert(M.checkAnswer(q,q.answerParts.map(v=>'0'+v)));
   assert(!M.checkAnswer(q,q.answerParts.map((v,i)=>i===0?v+1:v)));assert(!M.checkAnswer(q,q.answerParts.map(()=>'')));
  }else if(game==='directions'){
   if(q.category==='eight'){
    const dx=q.target.x-q.ref.x,dy=q.target.y-q.ref.y;assert(dx===0||dy===0||Math.abs(dx)===Math.abs(dy));
    const angle=(Math.atan2(dx,-dy)*180/Math.PI+360)%360;
    assert.equal(q.answer,['北','东北','东','东南','南','西南','西','西北'][Math.round(angle/45)%8]);
    assert(q.landmarks.some(x=>x.name===q.ref.name&&x.x===q.ref.x&&x.y===q.ref.y));
    assert(q.landmarks.some(x=>x.name===q.target.name&&x.x===q.target.x&&x.y===q.target.y));
   }else{
    const point=a=>a.center?[0,0]:[Math.sin(a.clock*Math.PI/6),-Math.cos(a.clock*Math.PI/6)];
    const a=point(q.ref),b=point(q.target),bearing=(Math.atan2(b[0]-a[0],a[1]-b[1])*180/Math.PI+360)%360;
    const relative=(bearing-q.heading*30+360)%360;assert.equal(q.answer,Math.round(relative/30)%12||12);
   }
  }
 }
 for(let i=0;i<100;i++){
  const players=M.roundFor(game),ids=players.flat().map(q=>q.id);assert.equal(new Set(ids).size,40,'players must not overlap');
  for(const round of players){assert.equal(round.length,20);assert.deepEqual(count(round),roundCounts[game]);if(game==='measure')assert.equal(round.filter(q=>q.variant==='zero').length,10);}
 }
 console.log(game,'PASS 100 authored questions, independent answers, source, unique content and 100 stratified rounds');
}
