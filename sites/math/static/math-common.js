(function(root,factory){
 const banks=typeof module==='object'&&module.exports?require('./math-banks.js'):root.MathBanks;
 const api=factory(banks);if(typeof module==='object'&&module.exports)module.exports=api;root.MathCommon=api;
})(typeof globalThis!=='undefined'?globalThis:this,function(banks){
 'use strict';
 const FACTORS=Object.freeze({'毫米':1,'厘米':10,'分米':100,'米':1000,'千米':1000000});
 function shuffle(list){const out=[...list];for(let i=out.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[out[i],out[j]]=[out[j],out[i]];}return out;}
 const quotas={conversion:{single:12,mixed:8},mental:{add2:5,sub2:5,add3:5,sub3:5},units:{'毫米':4,'厘米':4,'分米':4,'米':4,'千米':4},directions:{eight:10,clock:10}};
 function roundFor(game){
  const bank=banks[game];if(!bank||bank.length!==100)throw Error('题库未完整加载，请刷新页面。');
  const players=[[],[]];
  if(game==='measure'){
   for(const [category,count] of Object.entries({'single-mm':2,'single-cm':2,'single-dm':2,'cm-mm':6,'dm-cm':4,'dm-mm':4})){
    for(const variant of ['zero','offset']){
     const pool=shuffle(bank.filter(q=>q.category===category&&q.variant===variant)),take=count/2;
     if(pool.length<take*2)throw Error('测量题量不足。');
     for(let p=0;p<2;p++)players[p].push(...pool.slice(p*take,(p+1)*take));
    }
   }
  }else{
   for(const [category,count] of Object.entries(quotas[game])){
    const pool=shuffle(bank.filter(q=>q.category===category));if(pool.length<count*2)throw Error('分类题量不足。');
    for(let p=0;p<2;p++)players[p].push(...pool.slice(p*count,(p+1)*count));
   }
  }
  return players.map(round=>shuffle(round.map(q=>({...q,options:q.options?shuffle(q.options):null}))));
 }
 function checkAnswer(q,value){
  if(q.kind!=='measure')return String(value)===String(q.answer);
  if(!Array.isArray(value)||value.length!==q.answerParts.length)return false;
  if(value.some(v=>!/^\d{1,5}$/.test(String(v))))return false;
  const parts=value.map(Number);
  if(parts.length===2&&parts[1]>=FACTORS[q.answerUnits[0]]/FACTORS[q.answerUnits[1]])return false;
  return parts.every((v,i)=>v===q.answerParts[i]);
 }
 return {banks,FACTORS,shuffle,roundFor,checkAnswer,PLAYER_QUESTIONS:20};
});
