import {Drawing,boxAt} from './drawing.js';
const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
function el(tag,text='',attrs={}){const n=document.createElement(tag);if(text)n.textContent=text;for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);return n;}
function btn(text,fn,cls=''){const b=el('button',text,{type:'button',class:cls});b.onclick=fn;return b;}
function field(name,input){const l=el('label',name);l.append(input);return l;}
function input(value,type='text'){const i=el('input','',{type});i.value=value;return i;}
const kinds=['objects','zones','states','events'];
export class DraftPanel {
 constructor(o){this.o=o;this.d=null;this.selected=null;this.visible=true;this.generation=0;this.poll=null;this.edit=null;
  $('draftRefresh').onclick=()=>this.load();$('draftOverlayToggle').onchange=()=>{this.visible=$('draftOverlayToggle').checked;this.draw();};$('draftFilter').onchange=()=>this.render();
  $('draftDialogClose').onclick=()=>$('draftDialog').close();$('draftDialog').addEventListener('close',()=>{this.edit=null;this.load();});
  $('draftAccept').onclick=()=>this.acceptEdit();
 }
 all(){return Object.fromEntries(kinds.flatMap(k=>(this.d?.annotation[k]||[]).map(x=>[x.id,{kind:k,item:x}])));}
 deps(ids,overrides={}){const all=this.all(),out=new Set(),queue=[...ids];while(queue.length){const id=queue.pop();if(out.has(id)||!all[id])continue;out.add(id);if(this.d.reviews[id]?.decision==='accepted')continue;const {kind,item:original}=all[id],item=overrides[id]||original;let refs=[];if(kind==='objects')refs=[item.zone_id];if(kind==='zones')refs=item.anchors;if(kind==='states')refs=[item.target_id];if(kind==='events')refs=[item.actor_id,item.target_id,item.zone_id];queue.push(...refs.filter(Boolean));}return [...out];}
 label(kind,x){const c=this.o.config();return x.name||(kind==='objects'?c.objects[x.label]:kind==='zones'?c.zones[x.label]:kind==='states'?c.states[x.value]:c.events[x.label])||x.label||x.value;}
 async load(){
  if(this.edit)return;
  clearTimeout(this.poll);const id=this.o.currentId();if(!id)return;const generation=++this.generation;
  try{const d=await this.o.api(`api/videos/${id}/draft`);if(generation!==this.generation||id!==this.o.currentId())return;this.d=d;this.render();this.draw();if(!d||d.status==='processing'||typeof d.diagnostics?.segmentation==='string')this.poll=setTimeout(()=>this.load(),12000);}catch(e){this.o.error(e.message);}
 }
 reset(){++this.generation;clearTimeout(this.poll);this.d=null;this.selected=null;$('draftOverlay').replaceChildren();this.render();}
 render(){
  const p=$('draftItems');p.replaceChildren();const d=this.d;
  if(!d){$('draftSummary').textContent='Предложений для этого видео пока нет.';return;}
  const all=this.all(),reviews=d.reviews||{},pending=Object.keys(all).filter(id=>!reviews[id]).length;
  const phase={processing:'Обрабатывается',done:'Черновик готов',failed:'Обработка прервана'}[d.status];const covered=d.coverage.windows?.length||0;
  $('draftSummary').textContent=`${phase} · предложений ${Object.keys(all).length}, ожидают проверки ${pending}. Проанализировано окон: ${covered}.`;
  $('draftCoverage').textContent=d.coverage.gaps?.length?'Есть пропуски обработки. Полный просмотр обязателен.':'Временная выборка не гарантирует обнаружение каждого события. Проверьте видео целиком.';
  const filter=$('draftFilter').value;
  for(const kind of ['events','states','zones','objects'])for(const x of d.annotation[kind]){
   const decision=reviews[x.id]?.decision||'pending';if(filter!=='all'&&filter!==decision&&filter!==kind)continue;
   const m=d.metadata[x.id]||{},card=el('div','',{class:'item draft-card'+(x.id===this.selected?' selected':'')});const top=el('div','',{class:'row'});
   const main=btn(this.label(kind,x),()=>this.preview(x.id),'item-main');main.append(el('small',`${{objects:'Объект',zones:'Зона',states:'Состояние',events:'Событие'}[kind]} · ${reviews[x.id]?.changed_since_review?'изменено после решения':{pending:'требует проверки',accepted:'принято',rejected:'отклонено'}[decision]}`));top.append(main);card.append(top);if(reviews[x.id]?.changed_since_review)card.append(el('p','Предложение модели изменилось после вашего решения. Новая версия не проверена; рабочая разметка сохранена. Исправляйте принятую запись в обычных вкладках.',{class:'muted'}));
   if('start'in x)card.append(el('p',`${this.o.time(x.start)}–${this.o.time(x.end)}`));if(x.note)card.append(el('p',x.note));if(m.uncertainty)card.append(el('p',m.uncertainty,{class:'muted'}));if(m.refinement_error)card.append(el('p','Уточнить границы автоматически не удалось; проверьте исходный интервал.',{class:'muted'}));
   if(m.transfer&&Object.values(m.transfer).some(Boolean)){const phases=el('details');phases.append(el('summary','Основания передачи'));for(const [k,n]of [['before','До'],['interaction','Взаимодействие'],['after','После']])if(m.transfer[k])phases.append(el('p',`${n}: ${m.transfer[k]}`));phases.append(el('p',m.transfer_support==='three_phase_hypothesis'?'Модель указала три фазы; требуется проверка.':'Недостаточно наблюдений для подтверждения передачи денег.'));card.append(phases);}
   if(m.segmentation)card.append(el('p',`Маска SAM 2.1 · ${m.segmentation.mode==='video_propagation'?'перенесена по видео':'опорный кадр'} · не проверена человеком`,{class:'muted'}));
   const times=m.evidence_times||('start'in x?[x.start,x.end]:x.keyframes?.slice(0,3).map(k=>k.time)||[]);const chips=el('div','',{class:'keyframes'});for(const t of [...new Set(times)].slice(0,3))chips.append(btn(this.o.time(t),()=>{this.selected=x.id;this.o.seek(t);this.draw();}));card.append(chips);
   if(decision==='pending'){const actions=el('div','',{class:'row'});actions.append(btn('Проверить / принять',()=>this.openEdit(x.id),'primary'),btn('Отклонить',()=>this.reject(x.id)));card.append(actions);}p.append(card);
  }
  if(!p.children.length)p.append(el('p','Нет предложений с выбранным фильтром.',{class:'empty'}));
 }
 preview(id){this.selected=id;const a=this.all()[id],m=this.d.metadata[id]||{};if(!a)return;const t=m.evidence_times?.[0]??a.item.start??a.item.keyframes?.[0]?.time??0;this.o.seek(t);this.render();this.draw();}
 draw(){
  const svg=$('draftOverlay');svg.replaceChildren();if(!this.d||!this.visible||this.o.tab()!=='draft')return;const media=this.o.media(),w=media.width,h=media.height,t=this.o.video.currentTime;svg.setAttribute('viewBox',`0 0 ${w} ${h}`);
  const add=(tag,attrs)=>{const n=document.createElementNS(NS,tag);for(const[k,v]of Object.entries(attrs))n.setAttribute(k,v);svg.append(n);return n;};
  const selected=this.selected?new Set(this.deps([this.selected])):null;
  for(const z of this.d.annotation.zones){if(this.d.reviews[z.id]?.decision==='rejected'||(selected&&!selected.has(z.id)))continue;add('polygon',{points:z.points.map(([x,y])=>`${x*w},${y*h}`).join(' '),fill:'#ffbd6520',stroke:'#ffbd65','stroke-width':2,'stroke-dasharray':'6 4','vector-effect':'non-scaling-stroke'});}
  for(const o of this.d.annotation.objects){if(this.d.reviews[o.id]?.decision==='rejected'||(selected&&!selected.has(o.id)))continue;const box=boxAt(o,t,media.fps);if(!box)continue;const[x,y,bw,bh]=box;add('rect',{x:x*w,y:y*h,width:bw*w,height:bh*h,fill:'none',stroke:'#ffbd65','stroke-width':2,'vector-effect':'non-scaling-stroke'});const k=o.keyframes.find(k=>Math.abs(k.time-t)<=Math.max(.025,.51/media.fps));for(const poly of k?.polygons||[])add('polygon',{points:poly.map(([x,y])=>`${x*w},${y*h}`).join(' '),fill:'#ffbd6545',stroke:'#ffe0a3','stroke-width':1,'vector-effect':'non-scaling-stroke'});}
 }
 async reject(id){
  const reviewer=$('reviewer').value.trim();if(!reviewer){this.o.error('Укажите имя проверяющего под видео.');$('reviewer').focus();return;}
  try{await this.o.flush();await this.o.api(`api/videos/${this.o.currentId()}/draft/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:this.o.revision(),draft_revision:this.d.revision,action:'reject',ids:[id],reviewer})});await this.load();}catch(e){this.o.error(e.message);}
 }
 openEdit(id){
  clearTimeout(this.poll);
  this.preview(id);this.o.video.pause();const {kind,item}=this.all()[id];this.edit={id,kind,draft_revision:this.d.revision,item:structuredClone(item)};const x=this.edit.item,c=this.o.config();$('draftDialogTitle').textContent='Проверка: '+this.label(kind,x);$('draftEditFields').replaceChildren();$('draftEditError').textContent='';
  const fields=$('draftEditFields'),select=(entries,value,fn)=>{const s=el('select');for(const[k,v]of entries)s.append(el('option',v,{value:k}));s.value=value;s.onchange=()=>fn(s.value);return s;};
  if(kind==='objects'||kind==='zones'){const name=input(x.name);name.oninput=()=>x.name=name.value;fields.append(field('Название',name));fields.append(field('Класс',select(Object.entries(kind==='objects'?c.objects:c.zones).filter(([k])=>k!=='key'),x.label,v=>x.label=v)));}
  if(kind==='objects')fields.append(field('Связанная зона',select([['','Не выбрана'],...this.d.annotation.zones.map(z=>[z.id,z.name||c.zones[z.label]])],x.zone_id,v=>{x.zone_id=v;this.confirmations();})));
  if(kind==='zones'){
   const anchors=el('fieldset');anchors.append(el('legend','Опорные объекты'));
   for(const o of this.d.annotation.objects){const check=input('','checkbox');check.checked=x.anchors.includes(o.id);check.onchange=()=>{x.anchors=check.checked?[...x.anchors,o.id]:x.anchors.filter(id=>id!==o.id);this.confirmations();};anchors.append(field(`${o.name||c.objects[o.label]} · ${o.id}`,check));}fields.append(anchors);
  }
  if(kind==='states'||kind==='events'){
   fields.append(field(kind==='states'?'Состояние':'Событие',select(Object.entries(kind==='states'?c.states:c.events),x.value||x.label,v=>{if(kind==='states')x.value=v;else x.label=v;this.confirmations();})));
   for(const[k,label]of [['start','Начало, с'],['end','Конец, с']]){const n=input(x[k],'number');n.step='.01';n.min=0;n.max=this.o.media().duration;n.oninput=()=>x[k]=Number(n.value);fields.append(field(label,n));}
   const targets=[['','Не выбран'],...Object.entries(this.all()).filter(([,v])=>['objects','zones'].includes(v.kind)).map(([id,v])=>[id,this.label(v.kind,v.item)])];fields.append(field('Объект / зона',select(targets,x.target_id,v=>{x.target_id=v;this.confirmations();})));
   if(kind==='events'){
    const actors=[['','Неизвестен'],...this.d.annotation.objects.filter(o=>o.label==='person').map(o=>[o.id,`${o.name||'Человек'} · ${o.id}`])];fields.append(field('Участник',select(actors,x.actor_id,v=>x.actor_id=v)));
    fields.append(field('Зона события',select([['','Не выбрана'],...this.d.annotation.zones.map(z=>[z.id,z.name||c.zones[z.label]])],x.zone_id,v=>{x.zone_id=v;this.confirmations();})));
   }
   const note=el('textarea','',{rows:3});note.value=x.note;note.oninput=()=>x.note=note.value;fields.append(field('Наблюдение',note));
  }
  const editor=$('draftGeometryEditor');editor.hidden=!['objects','zones'].includes(kind);
  if(!editor.hidden){
   const t=x.keyframes?.[0]?.time??this.o.video.currentTime;this.o.seek(t);$('draftEditImage').src=`api/videos/${this.o.currentId()}/frame?t=${t}`;const frame=$('draftEditFrame');frame.style.aspectRatio=`${this.o.media().width}/${this.o.media().height}`;
   const a={objects:kind==='objects'?[x]:[],zones:kind==='zones'?[x]:[]};
   const oldSvg=$('draftEditOverlay');oldSvg.replaceWith(oldSvg.cloneNode(false));
   this.editDrawing=new Drawing($('draftEditOverlay'),this.o.video,{data:()=>a,media:()=>this.o.media(),selected:()=>id,visible:()=>true,label:()=>x.name||x.label,select:()=>{},toolChanged:()=>{},hint:()=>{},changeShape:(oid,coords,k)=>{if(k==='zone')x.points=coords;else {const key=x.keyframes.find(k=>Math.abs(k.time-this.o.video.currentTime)<.05);if(key){key.box=coords;key.polygons=[];key.mask_source='';}}this.editDrawing.render();}});this.editDrawing.render();
   $('draftEditKeyframes').replaceChildren();for(const k of x.keyframes||[])$('draftEditKeyframes').append(btn(this.o.time(k.time),()=>{this.o.seek(k.time);$('draftEditImage').src=`api/videos/${this.o.currentId()}/frame?t=${k.time}`;this.editDrawing.render();}));
  }
  $('confirmDraftZones').checked=false;$('confirmDraftVisibility').checked=false;this.confirmations();$('draftDialog').showModal();
 }
 confirmations(){if(!this.edit)return;const ids=this.deps([this.edit.id],{[this.edit.id]:this.edit.item}),all=this.all();const pending=ids.filter(id=>this.d.reviews[id]?.decision!=='accepted');$('draftDependencies').textContent='Будут добавлены: '+pending.map(id=>this.label(all[id].kind,id===this.edit.id?this.edit.item:all[id].item)).join(', ');$('draftZoneConfirmation').hidden=!pending.some(id=>all[id].kind==='zones');$('draftVisibilityConfirmation').hidden=!pending.some(id=>{const x=id===this.edit.id?this.edit.item:all[id].item;return all[id].kind==='states'&&x.value.endsWith('_absent');});}
 async acceptEdit(){
  const reviewer=$('reviewer').value.trim();if(!reviewer){$('draftEditError').textContent='Укажите имя проверяющего под видео, затем повторите.';return;}
  const edit=this.edit;if(!edit)return;$('draftAccept').disabled=true;
  try{await this.o.flush();await this.o.api(`api/videos/${this.o.currentId()}/draft/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({revision:this.o.revision(),draft_revision:edit.draft_revision,action:'accept',ids:[edit.id],reviewer,overrides:{[edit.id]:edit.item},confirm_zones:$('confirmDraftZones').checked,confirm_visibility:$('confirmDraftVisibility').checked})});$('draftDialog').close();await this.o.reload();this.o.showTab('draft');}catch(e){$('draftEditError').textContent=e.message;}finally{$('draftAccept').disabled=false;}
 }
}
