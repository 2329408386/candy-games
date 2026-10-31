(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;root.MathMeasureObjects=api;})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
const catalog=Object.freeze({
 eraser:{name:'橡皮',min:17,max:80,ideal:45},pencil:{name:'铅笔',min:60,max:200,ideal:160},
 fountainPen:{name:'钢笔',min:110,max:180,ideal:140},crayon:{name:'蜡笔',min:35,max:100,ideal:70},
 chalk:{name:'粉笔',min:40,max:100,ideal:65},paperclip:{name:'回形针',min:17,max:55,ideal:30},
 glueStick:{name:'胶棒',min:50,max:120,ideal:85},paintbrush:{name:'画笔',min:180,max:300,ideal:230},
 shoelace:{name:'鞋带',min:400,max:1000,ideal:700},ribbon:{name:'丝带',min:300,max:1000,ideal:550}
});
const palettes=[['#da6b79','#f5bac2','#8e4654'],['#5688b4','#bfd9ee','#355b7b'],['#d49937','#f7d98f','#90601d'],['#8b6cb2','#d8c6ec','#5f467d']];
const clamp=(n,a,b)=>Math.max(a,Math.min(b,n)),f=n=>Number(n.toFixed(3));
function draw(q,{xStart,xEnd,top=40,prefix='object'}={}){
 const type=q.objectType;if(!catalog[type])throw Error('物品图片未配置，请刷新页面。');
 const w=xEnd-xStart;if(!(w>0))throw Error('物品测量端点不正确。');
 const [color,light,dark]=palettes[(q.objectVariant||0)%palettes.length];
 const heights={eraser:clamp(w*.36,38,100),pencil:clamp(w*.09,24,42),fountainPen:clamp(w*.105,26,46),crayon:clamp(w*.15,25,48),chalk:clamp(w*.2,22,45),paperclip:clamp(w*.32,30,70),glueStick:clamp(w*.24,35,74),paintbrush:clamp(w*.08,24,38),shoelace:clamp(w*.045,18,26),ribbon:clamp(w*.13,30,56)};
 const h=heights[type],p=(...ns)=>ns.map(f).join(' ');
 const rect=(x,y,width,height,fill,rx=0,stroke='none',sw=1)=>`<rect x="${f(x)}" y="${f(y)}" width="${f(width)}" height="${f(height)}" rx="${f(rx)}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}"/>`;
 const path=(d,fill,stroke='none',sw=1,extra='')=>`<path d="${d}" fill="${fill}" stroke="${stroke}" stroke-width="${sw}" ${extra}/>`;
 let body='';
 if(type==='eraser'){
  const cut=Math.min(h*.2,w*.12),cover=w*.49;
  body=path(`M${p(0,cut)}L${p(cut,0)}H${f(w-cut)}L${p(w,cut)}V${f(h-cut)}L${p(w-cut,h)}H${f(cut)}L${p(0,h-cut)}Z`,'#f3b8bc','#b47b83',1.8);
  body+=path(`M${p(cut,0)}H${f(w-cut)}L${p(w,cut)}H0Z`,'#ffe3e5');
  body+=rect(w*.29,0,cover,h,color,2);
  body+=rect(w*.34,h*.22,cover*.78,h*.51,'#fff8ea',3);
  body+=path(`M${p(w*.40,h*.4)}H${f(w*.65)}M${p(w*.40,h*.58)}H${f(w*.58)}`,'none',dark,2);
  body+=path(`M${p(cut,h)}H${f(w-cut)}L${p(w,h-cut)}H0Z`,'#d799a1');
 }else if(type==='pencil'){
  const tip=Math.min(w*.23,h*1.35),tail=Math.min(w*.14,h*.85),wood=w-tip;
  body=rect(0,h*.10,tail,h*.8,'#e9a2a7',h*.25,'#b77b81',1);
  body+=rect(tail*.78,h*.08,tail*.33,h*.84,'#c7cbd1',1,'#89909a',1);
  body+=path(`M${p(tail,h*.12)}H${f(wood)}L${p(wood,h*.88)}H${f(tail)}Z`,'#e9b446','#9a762d',1);
  body+=rect(tail*1.13,h*.20,Math.max(1,wood-tail*1.13),h*.19,'#ffdc73');
  body+=rect(tail*1.13,h*.67,Math.max(1,wood-tail*1.13),h*.14,'#c98d28');
  body+=path(`M${p(wood,h*.12)}L${p(w,h*.5)}L${p(wood,h*.88)}Z`,'#e8c294','#9f805f',1);
  body+=path(`M${p(w-tip*.22,h*.415)}L${p(w,h*.5)}L${p(w-tip*.22,h*.585)}Z`,'#45424a');
 }else if(type==='fountainPen'){
  const cap=Math.min(w*.32,h*2.2),tip=Math.min(w*.14,h*.95),barrelEnd=w-tip;
  body=rect(0,h*.12,cap,h*.8,color,h*.3,dark,1.3);
  body+=rect(cap*.15,h*.03,cap*.7,h*.12,'#c8cdd2',h*.04,'#7e8790',.8);
  body+=rect(cap*.76,h*.11,cap*.08,h*.82,'#e1bf64',1);
  body+=path(`M${p(cap*.88,h*.17)}H${f(barrelEnd-h*.1)}Q${p(barrelEnd,h*.17,barrelEnd,h*.34)}V${f(h*.69)}Q${p(barrelEnd,h*.84,barrelEnd-h*.1,h*.84)}H${f(cap*.88)}Z`,color,dark,1.3);
  body+=rect(cap*1.04,h*.25,Math.max(1,barrelEnd-cap*1.04-h*.15),h*.13,light,2);
  body+=path(`M${p(barrelEnd,h*.29)}L${p(w,h*.50)}L${p(barrelEnd,h*.73)}L${p(barrelEnd-tip*.15,h*.5)}Z`,'#e8c86c','#987625',1);
  body+=path(`M${p(barrelEnd+tip*.25,h*.5)}H${f(w)}`,'none','#6f5d35',.9);
 }else if(type==='crayon'){
  const tip=Math.min(w*.17,h*.8);
  body=path(`M${p(0,h*.17)}H${f(w-tip)}L${p(w,h*.5)}L${p(w-tip,h*.83)}H0Z`,color,dark,1.4);
  body+=rect(w*.13,h*.10,w*.57,h*.8,light,2,dark,1);
  body+=rect(w*.17,h*.17,w*.08,h*.66,color);
  body+=rect(w*.58,h*.17,w*.08,h*.66,color);
  body+=path(`M${p(w*.31,h*.35)}L${p(w*.39,h*.65)}L${p(w*.47,h*.35)}L${p(w*.55,h*.65)}`,'none',dark,2);
 }else if(type==='chalk'){
  body=path(`M${p(0,h*.23)}Q${p(0,0,h*.25,0)}H${f(w-h*.23)}L${p(w,h*.18)}V${f(h*.82)}L${p(w-h*.23,h)}H${f(h*.25)}Q${p(0,h,0,h*.77)}Z`,'#f5efe1','#a9a494',1.8);
  body+=path(`M${p(w*.05,h*.28)}Q${p(w*.45,h*.12,w*.94,h*.26)}`,'none','#fffefa',h*.16);
  for(let i=0;i<9;i++)body+=`<circle cx="${f(w*(.08+i*.10))}" cy="${f(h*(.58+(i%3)*.075))}" r="${f(h*.025)}" fill="#d7cfbc"/>`;
 }else if(type==='paperclip'){
  const sw=Math.min(6,h*.1),r=h*.44;
  const d=`M${p(w*.34,h*.66)}H${f(w-r)}Q${p(w-sw/2,h*.66,w-sw/2,h*.42)}Q${p(w-sw/2,sw/2,w-r,sw/2)}H${f(r)}Q${p(sw/2,sw/2,sw/2,h*.5)}Q${p(sw/2,h-sw/2,r,h-sw/2)}H${f(w-r*1.2)}Q${p(w-sw*1.6,h-sw/2,w-sw*1.6,h*.55)}Q${p(w-sw*1.6,h*.28,w-r*1.2,h*.28)}H${f(r*1.3)}Q${p(r*.67,h*.28,r*.67,h*.53)}`;
  body=path(d,'none','#7d91a3',sw,'stroke-linecap="round" stroke-linejoin="round"');
  body+=path(d,'none','#d7e3ed',sw*.45,'stroke-linecap="round" stroke-linejoin="round"');
 }else if(type==='glueStick'){
  const cap=Math.min(w*.23,h*.8);
  body=rect(cap*.65,h*.08,w-cap*.65,h*.84,'#f4d66b',h*.13,'#af8e2d',1.5);
  body+=rect(0,0,cap,h,color,h*.13,dark,1.5);
  for(let i=1;i<5;i++)body+=path(`M${p(cap*i/6,h*.08)}V${f(h*.92)}`,'none',light,1);
  body+=rect(cap*1.2,h*.20,Math.max(1,w-cap*1.5),h*.6,'#fff7de',3);
  body+=path(`M${p(cap*1.5,h*.40)}H${f(w*.81)}M${p(cap*1.5,h*.59)}H${f(w*.7)}`,'none',dark,2);
 }else if(type==='paintbrush'){
  const metal=w*.73,bristles=w*.83;
  body=path(`M${p(0,h*.48)}Q${p(w*.03,h*.32,w*.15,h*.33)}L${p(metal,h*.23)}V${f(h*.77)}L${p(w*.15,h*.67)}Q${p(w*.03,h*.68,0,h*.52)}Z`,'#b97943','#84512f',1.3);
  body+=path(`M${p(w*.05,h*.45)}L${p(metal,h*.35)}`,'none','#e7af75',1.4);
  body+=rect(metal,h*.18,bristles-metal,h*.64,'#ccd3d7',2,'#7f8d95',1);
  body+=path(`M${p(bristles,h*.20)}Q${p(w*.94,h*.16,w,h*.5)}Q${p(w*.94,h*.84,bristles,h*.80)}Z`,'#785034','#543824',1);
  for(let i=0;i<4;i++)body+=path(`M${p(bristles,h*(.30+i*.13))}Q${p(w*.94,h*(.32+i*.1),w,h*.5)}`,'none','#bf9766',.7);
 }else if(type==='shoelace'){
  const end=Math.min(w*.08,h*1.25),cy=h*.5;
  body=rect(end*.5,h*.25,w-end,h*.5,color,h*.2,dark,1);
  for(let x=end;x<w-end;x+=h*.45)body+=path(`M${p(x,h*.27)}L${p(Math.min(w-end,x+h*.37),h*.73)}`,'none',light,1.4);
  body+=rect(0,h*.20,end,h*.6,'#c9d2d8',h*.12,'#7d8c96',1);
  body+=rect(w-end,h*.20,end,h*.6,'#c9d2d8',h*.12,'#7d8c96',1);
  body+=path(`M${p(end*.22,cy)}H${f(end*.82)}M${p(w-end*.82,cy)}H${f(w-end*.22)}`,'none','#edf4f8',1);
 }else if(type==='ribbon'){
  const notch=h*.35;
  body=path(`M${p(0,h*.08)}Q${p(w*.35,h*.22,w,h*.08)}L${p(w-notch,h*.5)}L${p(w,h*.92)}Q${p(w*.65,h*.78,0,h*.92)}L${p(notch,h*.5)}Z`,color,dark,1.2);
  body+=path(`M${p(h*.2,h*.30)}Q${p(w*.45,h*.44,w-h*.2,h*.30)}L${p(w-h*.33,h*.44)}Q${p(w*.45,h*.55,h*.33,h*.44)}Z`,light);
  body+=path(`M${p(h*.15,h*.79)}Q${p(w*.55,h*.66,w-h*.15,h*.79)}`,'none',light,1.2);
 }
 const id=('measure-object-'+prefix+'-'+q.id).replace(/[^a-zA-Z0-9_-]/g,'');
 const svg=`<defs><clipPath id="${id}"><rect x="0" y="0" width="${f(w)}" height="${f(h)}"/></clipPath></defs><g data-measure-object="${type}" data-object-start="${f(xStart)}" data-object-end="${f(xEnd)}" transform="translate(${f(xStart)} ${f(top)})" clip-path="url(#${id})">${body}</g>`;
 return {svg,height:h,start:xStart,end:xEnd,type};
}
return {catalog,draw};
});
