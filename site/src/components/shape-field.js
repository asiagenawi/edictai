// The home page field: small geometric shapes and school supplies in the desk
// palette fill the page and flow in along a diagonal wave. About one in twelve
// shapes shakes on its own, and shapes near the pointer scatter and spring back.
// With reduced motion the field is drawn once, still.
(()=>{
const css=n=>getComputedStyle(document.documentElement).getPropertyValue("--"+n).trim();
const PAL=["red","yellow","blue","sky","green","mint","pink","graphite","butter"];
function rng(s){let x=s>>>0||1;return()=>((x=Math.imul(x^x>>>15,2246822507),x=Math.imul(x^x>>>13,3266489909),(x^=x>>>16)>>>0)/4294967296);}
let CC={};const cc=n=>CC[n]??(CC[n]=css(n));
function shape(c,k,x,y,s,col,bg){c.fillStyle=col;c.beginPath();const h=s/2,r=s*.47;
  switch(k){
    case 0:c.arc(x+h,y+h,r,0,7);break;
    case 1:c.moveTo(x+h,y+h-r);c.arc(x+h,y+h,r,-Math.PI/2,Math.PI/2);break;
    case 2:c.moveTo(x,y+s);c.arc(x,y+s,s*.96,-Math.PI/2,0);break;
    case 3:c.rect(x+.04*s,y+.04*s,s*.92,s*.92);break;
    case 4:c.moveTo(x+h,y);c.lineTo(x+s,y+h);c.lineTo(x+h,y+s);c.lineTo(x,y+h);break;
    case 5:c.arc(x+h,y+h,r,0,7);c.fill();c.fillStyle=bg;c.beginPath();c.arc(x+h,y+h,r*.45,0,7);break;
    case 6:c.rect(x+.04*s,y+h,s*.92,s*.46);c.moveTo(x+.04*s,y+h);c.arc(x+h,y+h,s*.46,Math.PI,0);break;
    case 7:c.moveTo(x+h,y+s*.1);c.lineTo(x+s*.9,y+s*.9);c.lineTo(x+s*.1,y+s*.9);break;
    case 8:{ // pencil, diagonal
      c.save();c.translate(x+h,y+h);c.rotate(-Math.PI/4);const L=s*1.15,w=s*.26;
      c.fillStyle=cc("yellow");c.fillRect(-L/2+w,-w/2,L-w*2.2,w);c.fillStyle=cc("wood");c.beginPath();c.moveTo(-L/2,0);c.lineTo(-L/2+w,-w/2);c.lineTo(-L/2+w,w/2);c.fill();
      c.fillStyle=cc("graphite");c.beginPath();c.moveTo(-L/2,0);c.lineTo(-L/2+w*.4,-w*.2);c.lineTo(-L/2+w*.4,w*.2);c.fill();
      c.fillStyle=cc("metal");c.fillRect(L/2-w*1.2,-w/2,w*.4,w);c.fillStyle=cc("pink");c.fillRect(L/2-w*.8,-w/2,w*.8,w);c.restore();return;}
    case 9:{ // paper clip
      c.save();c.strokeStyle=col;c.lineWidth=s*.09;c.lineCap="round";const a=x+s*.32,b=x+s*.68,t=y+s*.12,u=y+s*.88;
      c.beginPath();c.moveTo(b,y+s*.35);c.lineTo(b,u-s*.18);c.arc(h+x,u-s*.18,s*.18,0,Math.PI);c.lineTo(a,t+s*.14);c.arc(x+s*.45,t+s*.14,s*.13,Math.PI,0);c.lineTo(x+s*.58,u-s*.3);c.stroke();c.restore();return;}
    case 10:{ // eraser
      c.save();c.translate(x+h,y+h);c.rotate(-.35);c.fillStyle=cc("pink");c.fillRect(-s*.42,-s*.2,s*.84,s*.4);c.fillStyle=cc("blue");c.globalAlpha*=.55;c.fillRect(-s*.42,-s*.2,s*.32,s*.4);c.restore();return;}
    case 11:{ // ruler
      c.save();c.translate(x+h,y+h);c.rotate(Math.PI/4);c.fillStyle=cc("wood");c.fillRect(-s*.6,-s*.17,s*1.2,s*.34);c.strokeStyle=cc("graphite");c.lineWidth=.6;
      for(let i=-4;i<=4;i++){c.beginPath();c.moveTo(i*s*.12,-s*.17);c.lineTo(i*s*.12,-s*.17+(i%2?s*.1:s*.16));c.stroke();}c.restore();return;}
    case 12:{ // laptop: coloured screen in a dark lid, light base
      c.fillStyle=cc("graphite");c.fillRect(x+s*.16,y+s*.18,s*.68,s*.48);
      c.fillStyle=col;c.fillRect(x+s*.21,y+s*.23,s*.58,s*.38);
      c.fillStyle=cc("metal");c.beginPath();c.moveTo(x+s*.08,y+s*.72);c.lineTo(x+s*.92,y+s*.72);c.lineTo(x+s*.98,y+s*.82);c.lineTo(x+s*.02,y+s*.82);c.fill();return;}
    case 13:{ // desktop monitor on a stand
      c.fillStyle=cc("graphite");c.fillRect(x+s*.1,y+s*.12,s*.8,s*.56);
      c.fillStyle=col;c.fillRect(x+s*.15,y+s*.17,s*.7,s*.46);
      c.fillStyle=cc("metal");c.fillRect(x+s*.45,y+s*.68,s*.1,s*.14);c.fillRect(x+s*.3,y+s*.82,s*.4,s*.06);return;}
  }c.fill();}
const cv=document.getElementById("field"),ctx=cv.getContext("2d");
const still=matchMedia("(prefers-reduced-motion: reduce)");
let items=[],t0=0,raf=0,W=0,H=0,S=18,px=-1e4,py=-1e4;
// Lay out the field once per size. The whole page fills; a few cells are left empty or soft for texture,
// and about one in twelve items is a "shaker" that wiggles on its own.
function layout(){const dpr=Math.min(2,devicePixelRatio||1);W=innerWidth;H=innerHeight;cv.width=W*dpr;cv.height=H*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);
  S=W<640?14:18;const step=S+2,r=rng(7);items=[];CC={};
  for(let y=0;y<H;y+=step)for(let x=0;x<W;x+=step){
    const fx=x/W,fy=y/H,p=.86;
    const a=r(),b=r(),c=r(),d=r(),e=r();if(a>p)continue;
    items.push({x,y,k:d<.08?8+Math.floor(b*6):Math.floor(b*8),col:PAL[Math.floor(c*PAL.length)],alpha:a>.74?.45:1,
      delay:(fx*.75+fy*.35)*1500+d*380, shake:e<.085, phase:e*400, speed:.6+b*.8, ox:0,oy:0});
  }}
const ease=t=>1-Math.pow(1-t,3);
const R=110, PUSH=34; // cursor scatter radius and distance, in px
function frame(now){const el=now-t0,bg=cc("ground"),cols={};for(const n of PAL)cols[n]=cc(n);
  ctx.clearRect(0,0,W,H);let entering=false,moving=false;const h=S/2,live=!still.matches;
  for(const it of items){let t=(el-it.delay)/650;if(t<=0){entering=true;continue;}if(t<1)entering=true;else t=1;
    // Scatter: push away from the cursor, then spring back.
    let tx=0,ty=0;if(live){const dx=it.x+h-px,dy=it.y+h-py,dd=Math.hypot(dx,dy);
      if(dd<R&&dd>0.01){const f=(1-dd/R);tx=dx/dd*PUSH*f*f*1.6;ty=dy/dd*PUSH*f*f*1.6;}}
    it.ox+=(tx-it.ox)*.14;it.oy+=(ty-it.oy)*.14;if(Math.abs(it.ox-tx)+Math.abs(it.oy-ty)>.2)moving=true;
    const e=ease(t),sc=.35+.65*e;
    const rot=live&&it.shake?Math.sin(now/1000*it.speed*6+it.phase)*.22*Math.max(0,Math.sin(now/1000*it.speed+it.phase)):0;
    ctx.save();ctx.globalAlpha=it.alpha*e;ctx.translate(it.x+h+it.ox,it.y+h+it.oy);if(rot)ctx.rotate(rot);ctx.scale(sc,sc);ctx.translate(-h,-h);
    shape(ctx,it.k,0,0,S,cols[it.col],bg);ctx.restore();}
  if(entering||moving||live)raf=requestAnimationFrame(frame);}
function draw(animate){cancelAnimationFrame(raf);layout();
  t0=animate&&!still.matches?performance.now():-1e9;raf=requestAnimationFrame(frame);}
addEventListener("pointermove",e=>{px=e.clientX;py=e.clientY;},{passive:true});
addEventListener("pointerleave",()=>{px=py=-1e4;});
document.addEventListener("pointerout",e=>{if(!e.relatedTarget){px=py=-1e4;}});

// The page may load before it is shown, so wait until it is really on screen.
function startWhenVisible(){layout();ctx.clearRect(0,0,W,H);
  const go=()=>setTimeout(()=>draw(true),250);
  const io=new IntersectionObserver(es=>{if(es.some(e=>e.isIntersecting)&&document.visibilityState==="visible"){io.disconnect();go();}});
  if(document.visibilityState==="visible")io.observe(cv);
  else document.addEventListener("visibilitychange",function v(){if(document.visibilityState==="visible"){document.removeEventListener("visibilitychange",v);io.observe(cv);}});}
if(still.matches)draw(false);else if(document.readyState==="complete")startWhenVisible();else addEventListener("load",startWhenVisible,{once:true});
const word=document.querySelector(".word");if(word)word.addEventListener("click",()=>draw(true));
let t;addEventListener("resize",()=>{clearTimeout(t);t=setTimeout(()=>draw(false),120)});
})();
