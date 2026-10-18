const NS='http://www.w3.org/2000/svg';
const clamp=(n,a=0,b=1)=>Math.max(a,Math.min(b,n));
export function boxAt(o,t,fps=30){
  const keys=[...o.keyframes].sort((a,b)=>a.time-b.time);
  const exact=keys.find(k=>Math.abs(k.time-t)<=Math.max(.025,.51/fps));
  if(exact)return [...exact.box];
  if(!o.interpolate)return null;
  for(let i=1;i<keys.length;i++)if(keys[i-1].time<t && t<keys[i].time){const f=(t-keys[i-1].time)/(keys[i].time-keys[i-1].time);return keys[i-1].box.map((v,j)=>v+(keys[i].box[j]-v)*f);}
  return null;
}
export class Drawing {
  constructor(svg,video,options){
    this.svg=svg;this.video=video;this.o=options;this.tool='select';this.points=[];this.drag=null;this.keyTarget=null;
    svg.addEventListener('pointerdown',e=>this.down(e));svg.addEventListener('pointermove',e=>this.move(e));svg.addEventListener('pointerup',e=>this.up(e));svg.addEventListener('pointercancel',()=>this.cancel());
  }
  data(){return this.o.data();}
  size(){return this.o.media()||{width:1000,height:1000,fps:30};}
  point(e){const r=this.svg.getBoundingClientRect();return [clamp((e.clientX-r.left)/r.width),clamp((e.clientY-r.top)/r.height)];}
  setTool(tool,keyTarget=null){this.tool=tool;this.keyTarget=keyTarget;this.points=[];this.drag=null;this.svg.classList.toggle('drawing',tool!=='select');this.o.toolChanged(tool,keyTarget);this.render();}
  cancel(){this.points=[];this.drag=null;this.setTool('select');}
  el(tag,attrs,parent=this.svg){const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);parent.append(n);return n;}
  render(){
    const a=this.data();if(!a)return;const {width:w,height:h,fps}=this.size(),scale=Math.max(w,h)/650;
    this.svg.setAttribute('viewBox',`0 0 ${w} ${h}`);this.svg.replaceChildren();
    if(!this.o.visible())return;
    const selected=this.o.selected();
    for(const z of a.zones){
      const pts=(this.drag?.id===z.id?this.drag.preview:z.points),active=z.id===selected;
      this.el('polygon',{points:pts.map(([x,y])=>`${x*w},${y*h}`).join(' '),fill:active?'#1dab7740':'#42a18e22',stroke:z.confirmed?'#55c9a0':'#ffbd65','stroke-width':active?2.5:1.5,'stroke-dasharray':z.confirmed?'':'6 4',class:'annotation-shape','data-id':z.id});
      const text=this.el('text',{x:pts[0][0]*w+4,y:pts[0][1]*h+17*scale,style:`font-size:${13*scale}px`});text.textContent=z.name||this.o.label(z.label,true);
      if(active&&this.tool==='select')pts.forEach(([x,y],i)=>this.el('circle',{cx:x*w,cy:y*h,r:5*scale,class:'handle','data-id':z.id,'data-point':i}));
    }
    for(const o of a.objects){
      const box=this.drag?.id===o.id?this.drag.preview:boxAt(o,this.video.currentTime,fps);if(!box)continue;
      const [x,y,bw,bh]=box,active=o.id===selected;
      const key=o.keyframes.find(k=>Math.abs(k.time-this.video.currentTime)<=Math.max(.025,.51/fps));
      for(const poly of key?.polygons||[])this.el('polygon',{points:poly.map(([px,py])=>`${px*w},${py*h}`).join(' '),fill:'#77ceff35',stroke:'#77ceff','stroke-width':1,class:'annotation-shape','data-id':o.id});
      this.el('rect',{x:x*w,y:y*h,width:bw*w,height:bh*h,fill:active?'#ffffff12':'transparent',stroke:active?'#ffce7b':'#77ceff','stroke-width':active?2.5:1.5,class:'annotation-shape','data-id':o.id});
      const text=this.el('text',{x:x*w+3,y:Math.max(15*scale,y*h-5),style:`font-size:${13*scale}px`});text.textContent=o.name||this.o.label(o.label,false);
      if(active&&this.tool==='select')[[x,y],[x+bw,y],[x+bw,y+bh],[x,y+bh]].forEach(([px,py],i)=>this.el('circle',{cx:px*w,cy:py*h,r:5*scale,class:'handle','data-id':o.id,'data-corner':i}));
    }
    if(this.points.length){this.el('polyline',{points:this.points.map(([x,y])=>`${x*w},${y*h}`).join(' '),fill:'#f3b34622',stroke:'#ffce7b','stroke-width':2,class:'draft'});for(const [x,y]of this.points)this.el('circle',{cx:x*w,cy:y*h,r:4*scale,fill:'#ffce7b',class:'draft'});}
    if(this.drag?.kind==='newbox'){const [x,y]=this.drag.start,[u,v]=this.drag.end;this.el('rect',{x:Math.min(x,u)*w,y:Math.min(y,v)*h,width:Math.abs(u-x)*w,height:Math.abs(v-y)*h,fill:'#ffffff18',stroke:'#ffce7b','stroke-width':2,class:'draft'});}
  }
  down(e){
    if(e.button!==0||!this.data()||!this.o.visible())return;this.video.pause();const p=this.point(e);
    if(this.tool==='zone'){this.points.push(p);this.render();this.o.hint(`Вершин: ${this.points.length}. Enter — завершить зону; Esc — отменить.`);return;}
    if(this.tool==='box'){this.drag={kind:'newbox',start:p,end:p};this.svg.setPointerCapture(e.pointerId);return;}
    const el=e.target.closest('[data-id]');if(!el){this.o.select(null);this.render();return;}
    const id=el.dataset.id;this.o.select(id);const a=this.data(),z=a.zones.find(x=>x.id===id),o=a.objects.find(x=>x.id===id),source=z?z.points:boxAt(o,this.video.currentTime,this.size().fps);
    if(!source)return;
    this.drag={kind:z?'zone':'box',id,start:p,source:structuredClone(source),preview:structuredClone(source),point:el.dataset.point===undefined?null:Number(el.dataset.point),corner:el.dataset.corner===undefined?null:Number(el.dataset.corner)};
    this.svg.setPointerCapture(e.pointerId);this.render();
  }
  move(e){
    if(!this.drag)return;const d=this.drag,p=this.point(e);if(d.kind==='newbox'){d.end=p;this.render();return;}
    const dx=p[0]-d.start[0],dy=p[1]-d.start[1];
    if(d.kind==='zone'){
      if(d.point!==null){d.preview=structuredClone(d.source);d.preview[d.point]=p;}
      else {const xs=d.source.map(p=>p[0]),ys=d.source.map(p=>p[1]),mx=clamp(dx,-Math.min(...xs),1-Math.max(...xs)),my=clamp(dy,-Math.min(...ys),1-Math.max(...ys));d.preview=d.source.map(([x,y])=>[x+mx,y+my]);}
    }else{
      const [x,y,w,h]=d.source;
      if(d.corner===null)d.preview=[clamp(x+dx,0,1-w),clamp(y+dy,0,1-h),w,h];
      else {const opposite=[[x+w,y+h],[x,y+h],[x,y],[x+w,y]][d.corner];d.preview=[Math.min(p[0],opposite[0]),Math.min(p[1],opposite[1]),Math.abs(p[0]-opposite[0]),Math.abs(p[1]-opposite[1])];}
    }this.render();
  }
  up(){
    const d=this.drag;if(!d)return;this.drag=null;
    if(d.kind==='newbox'){
      const [x,y]=d.start,[u,v]=d.end,box=[Math.min(x,u),Math.min(y,v),Math.abs(u-x),Math.abs(v-y)];
      if(box[2]>.003&&box[3]>.003){this.o.addBox(box,this.keyTarget);this.setTool('select');}else this.render();return;
    }
    if(JSON.stringify(d.source)!==JSON.stringify(d.preview)){
      if(d.kind==='box'&&(d.preview[2]<.003||d.preview[3]<.003)){this.render();return;}
      this.o.changeShape(d.id,d.preview,d.kind);
    }this.render();
  }
  finishZone(){
    if(this.tool!=='zone')return;
    if(this.points.length<3){this.o.hint('Для зоны нужны минимум три вершины.');return;}
    const p=this.points,area=Math.abs(p.reduce((s,[x,y],i)=>s+x*p[(i+1)%p.length][1]-y*p[(i+1)%p.length][0],0))/2;
    if(area<.00001){this.o.hint('Зона слишком мала. Добавьте вершины или нажмите Esc.');return;}
    this.o.addZone(structuredClone(p));this.setTool('select');
  }
}
