import {DraftPanel} from './drafts.js';
import {Drawing,boxAt} from './drawing.js';
import {EpisodePanel} from './episodes.js';
const $=id=>document.getElementById(id), video=$('video');
const state={config:null,videos:[],current:null,a:null,revision:0,selected:null,dirty:false,serial:0,saving:null,timer:null,history:[],edit:null,loading:false,conflict:false,tab:'episodes'};
const statuses={unreviewed:'○ Не размечено',in_progress:'◐ В работе',reviewed:'✓ Проверено'};
const uid=prefix=>`${prefix}_${crypto.randomUUID().slice(0,8)}`;
const now=()=>Math.round(video.currentTime*1000)/1000;
const duration=()=>state.current?.media.duration||0;
const time=t=>{t=Number(t)||0;return `${String(Math.floor(t/60)).padStart(2,'0')}:${(t%60).toFixed(2).padStart(5,'0')}`;};
const title=x=>x?.name||state.config?.objects[x?.label]||state.config?.zones[x?.label]||x?.label||'—';
const target=id=>[...(state.a?.objects||[]),...(state.a?.zones||[])].find(x=>x.id===id);
function node(tag,attrs={},text=''){const n=document.createElement(tag);for(const [k,v] of Object.entries(attrs)){if(k==='class')n.className=v;else n.setAttribute(k,v);}if(text)n.textContent=text;return n;}
function button(text,fn,cls=''){const b=node('button',{type:'button',class:cls},text);b.addEventListener('click',fn);return b;}
function error(message=''){ $('error').textContent=message;$('error').hidden=!message; }
function saveStatus(text,failed=false){$('saveStatus').textContent=text;$('saveStatus').classList.toggle('failed',failed);}
async function api(url,opts={}){const r=await fetch(url,opts);if(!r.ok){let detail;try{detail=(await r.json()).detail;}catch{detail=r.statusText;}if(Array.isArray(detail))detail=detail.map(x=>x.msg).join('; ');const e=new Error(detail||`Ошибка ${r.status}`);e.status=r.status;throw e;}return r.json();}
function options(select,items,empty=null){const previous=select.value;select.replaceChildren();if(empty!==null)select.append(node('option',{value:''},empty));for(const [value,label]of items)select.append(node('option',{value},label));if([...select.options].some(o=>o.value===previous))select.value=previous;}
function field(labelText,control){const l=node('label',{},labelText);l.append(control);return l;}
function checked(labelText,value,fn){const l=node('label',{class:'compact'});const i=node('input',{type:'checkbox'});i.checked=value;i.addEventListener('change',()=>fn(i.checked));l.append(i,document.createTextNode(labelText));return l;}
function stamp(){return {video_id:state.current.id,revision:state.revision,annotation:state.a};}
function backup(){if(!state.current)return;try{localStorage.setItem('cash-draft-'+state.current.id,JSON.stringify(stamp()));}catch{error('Браузер не смог сохранить резервный черновик. Проверьте сохранение на сервере.');}}
function mutate(fn,{review=false}={}){
  if(!state.a||state.loading)return;
  state.history.push(structuredClone(state.a));if(state.history.length>60)state.history.shift();
  fn(state.a);if(!review)state.a.status='in_progress';state.dirty=true;state.serial++;backup();saveStatus('Есть изменения…');
  clearTimeout(state.timer);state.timer=setTimeout(()=>flush().catch(()=>{}),650);renderAnnotations();
}
async function flush(){
  clearTimeout(state.timer);
  if(state.saving){await state.saving;if(state.dirty)return flush();return;}
  if(!state.dirty||!state.current)return;
  if(state.conflict)throw new Error('Сначала разрешите конфликт версий');
  const id=state.current.id,serial=state.serial,payload=JSON.stringify({revision:state.revision,annotation:state.a});saveStatus('Сохраняю…');
  const job=(async()=>{
    try{
      const r=await api(`api/videos/${id}/annotation`,{method:'PUT',headers:{'Content-Type':'application/json'},body:payload});
      state.revision=r.revision;
      if(state.serial===serial){state.dirty=false;try{localStorage.removeItem('cash-draft-'+id);}catch{}saveStatus(`Сохранено · v${r.revision}`);}
      else backup();
      const row=state.videos.find(v=>v.id===id);if(row)row.status=JSON.parse(payload).annotation.status;renderList();error();
    }catch(e){
      if(e.status===409){state.conflict=true;$('recovery').hidden=false;$('restoreDraft').hidden=true;}
      saveStatus('Не сохранено',true);error(e.message);throw e;
    }
  })();state.saving=job;
  try{await job;}finally{state.saving=null;}
  if(state.dirty)return flush();
}
function drawClass(tool){options($('drawClass'),Object.entries(tool==='zone'?state.config.zones:state.config.objects).filter(([k])=>k!=='key'));$('drawClass').setAttribute('aria-label',tool==='zone'?'Класс новой зоны':'Класс нового объекта');}
const drawing=new Drawing($('overlay'),video,{
  data:()=>state.a,media:()=>state.current?.media,selected:()=>state.selected,visible:()=>$('overlayToggle').checked,label:(l,z)=>(z?state.config.zones:state.config.objects)[l],
  select:id=>{state.selected=id;showTab('shapes');renderShapes();},hint:t=>$('toolHint').textContent=t,
  toolChanged:(tool,keyTarget)=>{document.querySelectorAll('[data-tool]').forEach(b=>{b.classList.toggle('active',b.dataset.tool===tool);b.setAttribute('aria-pressed',b.dataset.tool===tool);});drawClass(tool);$('toolHint').textContent=tool==='zone'?'Кликните вершины зоны. Enter — завершить; Esc — отменить.':tool==='box'?(keyTarget?'Нарисуйте новое положение выбранного объекта на этом кадре.':'Выберите класс и протяните рамку на текущем кадре.'):'Перетащите рамку, угол или вершину. Рамки отображаются только на размеченных кадрах.';},
  addBox:(box,id)=>mutate(a=>{
    if(id){const o=a.objects.find(x=>x.id===id);putKeyframe(o,box);}
    else {id=uid('o');a.objects.push({id,label:$('drawClass').value,name:'',role:'unknown',zone_id:'',keyframes:[{time:now(),box}],interpolate:false});}
    state.selected=id;showTab('shapes');
  }),
  addZone:points=>mutate(a=>{const id=uid('z');a.zones.push({id,label:$('drawClass').value,name:'',points,anchors:[],confirmed:true});state.selected=id;showTab('shapes');}),
  changeShape:(id,coords,kind)=>mutate(a=>{if(kind==='zone')a.zones.find(x=>x.id===id).points=coords;else putKeyframe(a.objects.find(x=>x.id===id),coords);})
});
function putKeyframe(o,box){const t=now(),old=o.keyframes.find(k=>Math.abs(k.time-t)<=Math.max(.025,.51/state.current.media.fps));if(old){old.box=box;old.polygons=[];old.mask_source='';}else o.keyframes.push({time:t,box});o.keyframes.sort((a,b)=>a.time-b.time);}
function renderList(){
  const q=$('search').value.toLowerCase(),filter=$('statusFilter').value,list=$('videoList');list.replaceChildren();
  for(const v of state.videos.filter(v=>v.filename.toLowerCase().includes(q)&&(filter==='all'||v.status===filter))){
    const b=button('',()=>openVideo(v.id).catch(e=>error(e.message)),'video-item'+(v.id===state.current?.id?' selected':''));b.dataset.videoId=v.id;b.setAttribute('aria-current',v.id===state.current?.id?'true':'false');b.append(node('strong',{},v.filename),node('small',{class:v.status==='reviewed'?'reviewed':''},statuses[v.status]));list.append(b);
  }
  if(!list.children.length)list.append(node('p',{class:'empty'},'Нет видео с такими условиями.'));
  $('count').textContent=state.videos.length;$('progress').textContent=`Проверено ${state.videos.filter(v=>v.status==='reviewed').length} из ${state.videos.length}`;
  document.querySelector('.library-footer').textContent=`${state.videos.length} видео · исходники только для чтения`;
}
async function openVideo(id,discard=false){
  if(state.loading)return;
  state.loading=true;document.body.classList.add('busy');video.pause();error();
  try{
    if(!discard)await flush();
    const detail=await api('api/videos/'+id);
    state.current=detail;draftPanel.reset();episodePanel.reset();state.a=detail.annotation;state.revision=detail.revision;state.dirty=false;state.conflict=false;state.selected=null;state.history=[];state.edit=null;state.serial=0;
    $('recovery').hidden=true;$('restoreDraft').hidden=false;
    try{const draft=JSON.parse(localStorage.getItem('cash-draft-'+id));if(draft&&JSON.stringify(draft.annotation)!==JSON.stringify(state.a))$('recovery').hidden=false;}catch{}
    if(!state.a.reviewer){try{$('reviewer').value=localStorage.getItem('cash-reviewer')||'';}catch{}}else $('reviewer').value=state.a.reviewer;
    $('verdict').value=state.a.verdict;$('fullReview').checked=state.a.full_video_reviewed;$('notes').value=state.a.notes;$('sceneId').value=state.a.scene_id||'';options($('copySource'),state.videos.filter(v=>v.id!==id).map(v=>[v.id,v.filename]),'Выберите видео');$('sameView').checked=false;
    $('videoName').textContent=detail.filename;$('videoMeta').textContent=`${time(duration())} · ${detail.media.width} × ${detail.media.height} · ${statuses[state.a.status]}`;
    $('seek').max=duration();$('seek').value=0;$('intervalStart').value=0;$('intervalEnd').value=duration().toFixed(3);
    $('mediaStatus').hidden=false;$('mediaStatus').textContent='Подготовка локального MP4…';video.src=`api/videos/${id}/media`;video.load();
    $('thresholdSafe').value=state.a.thresholds.safe;$('thresholdCash').value=state.a.thresholds.cash;$('thresholdDoor').value=state.a.thresholds.door;
    $('candidates').replaceChildren();resetInterval();drawing.setTool('select');renderAI();renderList();renderAnnotations();fitStage();
    saveStatus(`Сохранено · v${state.revision}`);history.replaceState(null,'','#'+id);draftPanel.load();
  }finally{state.loading=false;document.body.classList.remove('busy');}
}
function fitStage(){const m=state.current?.media;if(!m)return;const r=$('stage').getBoundingClientRect(),scale=Math.min(r.width/m.width,r.height/m.height);$('frame').style.width=m.width*scale+'px';$('frame').style.height=m.height*scale+'px';}
new ResizeObserver(fitStage).observe($('stage'));
function showTab(tab){state.tab=tab;document.querySelectorAll('[data-tab]').forEach(b=>{b.setAttribute('aria-selected',b.dataset.tab===tab);b.tabIndex=b.dataset.tab===tab?0:-1;});for(const t of ['episodes','ai','draft','shapes','intervals','rules'])$('panel-'+t).hidden=t!==tab;draftPanel.draw();}
function aiField(parent,label,value){if(value===undefined||value===null||value==='')return;parent.append(node('div',{class:'field-title'},label),node('p',{class:'ai-text'},String(value)));}
function renderAI(){
  const r=state.current,ai=r.ai||{},raw=r.audit||{},p=$('aiContent');p.replaceChildren();
  const t=Number(ai.timecode_sec??raw.timecode_sec);
  if(Number.isFinite(t)&&t>=0&&t<=duration())p.append(button(`↗ К эпизоду · ${time(t)}`,()=>seek(t),'wide'));
  const anchors=(raw.sampled_timestamps_sec||[]).map(Number).filter(t=>Number.isFinite(t)&&t>=0&&t<=duration());
  if(anchors.length){p.append(node('div',{class:'field-title'},'Опорные моменты ИИ'));const chips=node('div',{class:'keyframes'});for(const t of anchors)chips.append(button(time(t),()=>seek(t)));p.append(chips);}
  aiField(p,'Наблюдение',ai.observed_action||raw.observed_action||'Для этого видео нет ИИ-описания.');
  aiField(p,'Гипотеза',ai.possible_violation_type||raw.possible_violation_type);
  aiField(p,'Объяснение',ai.explanation||raw.why_possible_violation);
  aiField(p,'Ограничения',ai.limitations||raw.limitations);
  aiField(p,'Исходная метка организатора',r.source.label);
  aiField(p,'Группа / fold',[ai.group_id,ai.fold].filter(Boolean).join(' / '));
  $('aiEvent').disabled=!ai.observed_action&&!raw.observed_action;
  const prev=$('previousReview');prev.replaceChildren();const pr=r.previous_review||{};
  aiField(prev,'Статус в прежней таблице',pr.review_status||'Нет записи');aiField(prev,'Наблюдение',pr.observed_action);aiField(prev,'Заметки',pr.notes);aiField(prev,'Ограничения',pr.visibility_limitations);
}
function renderAnnotations(){if(!state.a)return;episodePanel.render();renderShapes();renderTargets();renderIntervals();drawing.render();renderTimeline();$('verdict').value=state.a.verdict;$('fullReview').checked=state.a.full_video_reviewed;$('markReviewed').textContent=state.a.status==='reviewed'?'Проверено ✓':'Проверено ✓';$('markReviewed').disabled=state.a.status==='reviewed';$('videoMeta').textContent=`${time(duration())} · ${state.current.media.width} × ${state.current.media.height} · ${statuses[state.a.status]}`;$('undo').disabled=!state.history.length;$('candidates').replaceChildren(node('p',{class:'muted'},'После изменения разметки нажмите «Проверить условия».'));}
function renderShapes(){
  if(!state.a)return;const list=$('shapeList');list.replaceChildren();$('shapeCount').textContent=state.a.objects.length+state.a.zones.length;
  for(const x of [...state.a.zones,...state.a.objects]){
    const zone=!!x.points,item=node('div',{class:'item'+(state.selected===x.id?' selected':'')});const row=node('div',{class:'row'});
    const b=button('',()=>{state.selected=x.id;if(!zone&&x.keyframes.length)seek(x.keyframes[0].time);renderShapes();drawing.render();},'item-main');
    b.append(node('strong',{},`${zone?'⬡':'▣'} ${title(x)}`),node('small',{},zone?(x.confirmed?'Зона подтверждена':'Черновая зона · подтвердите'):`${x.id} · кадров: ${x.keyframes.length}${x.role!=='unknown'?' · '+({cashier:'кассир',customer:'клиент',collector:'инкассатор'}[x.role]):''}`));
    row.append(b,button('×',()=>deleteShape(x.id),'delete'));row.lastChild.setAttribute('aria-label',`Удалить ${title(x)}`);item.append(row);list.append(item);
  }
  if(!list.children.length)list.append(node('p',{class:'empty'},'Поставьте видео на паузу. «Объект» — протянуть рамку; «Зона» — кликнуть вершины и нажать Enter.'));
  renderShapeEditor();
}
function deleteShape(id){mutate(a=>{a.objects=a.objects.filter(x=>x.id!==id);a.zones=a.zones.filter(x=>x.id!==id);a.objects.forEach(o=>{if(o.zone_id===id)o.zone_id='';});a.zones.forEach(z=>z.anchors=z.anchors.filter(x=>x!==id));a.states=a.states.filter(x=>x.target_id!==id);for(const e of a.events)for(const k of ['actor_id','target_id','zone_id'])if(e[k]===id)e[k]='';state.selected=null;});}
function renderShapeEditor(){
  const wrap=$('shapeEditor');wrap.replaceChildren();const x=target(state.selected);if(!x)return;const zone=!!x.points;wrap.className='shape-editor';wrap.append(node('h3',{},`Свойства · ${title(x)}`));
  const name=node('input',{value:x.name,placeholder:'Например: сейф у стены'});name.addEventListener('change',()=>mutate(()=>x.name=name.value));wrap.append(field('Название',name));
  const klass=node('select');options(klass,Object.entries(zone?state.config.zones:state.config.objects));klass.value=x.label;klass.addEventListener('change',()=>mutate(()=>x.label=klass.value));wrap.append(field('Класс',klass));
  if(zone){
    wrap.append(checked('Зона проверена вручную',x.confirmed,v=>mutate(()=>x.confirmed=v)));
    const anchors=node('div',{class:'anchor-list'});anchors.append(node('div',{class:'field-title'},'Опорные объекты'));
    for(const o of state.a.objects)anchors.append(checked(title(o),x.anchors.includes(o.id),v=>mutate(()=>x.anchors=v?[...x.anchors,o.id]:x.anchors.filter(id=>id!==o.id))));
    if(!state.a.objects.length)anchors.append(node('p',{class:'muted'},'Сначала отметьте сейф, стол или другие объекты.'));
    wrap.append(anchors,node('p',{class:'muted'},'Вершины перетаскиваются на видео. Подтверждение геометрии зоны не подтверждает роль человека.'));
    const coord=node('textarea',{rows:3,'aria-label':'Вершины зоны: x,y от 0 до 1'});coord.value=x.points.map(p=>p.map(v=>v.toFixed(4)).join(', ')).join('\n');wrap.append(field('Вершины (x,y от 0 до 1)',coord),button('Применить вершины',()=>{const points=coord.value.trim().split('\n').map(s=>s.split(',').map(Number));if(points.length<3||points.some(p=>p.length!==2||p.some(v=>!Number.isFinite(v)||v<0||v>1))){error('Нужны минимум три строки x,y с координатами от 0 до 1.');return;}mutate(()=>x.points=points);}));
  }else{
    if(x.label==='person'){const role=node('select');options(role,Object.entries({unknown:'Неизвестно',cashier:'Кассир',customer:'Клиент',collector:'Инкассатор'}));role.value=x.role;role.addEventListener('change',()=>mutate(()=>x.role=role.value));wrap.append(field('Роль (подтверждайте по контексту)',role));}
    const zoneSelect=node('select');options(zoneSelect,state.a.zones.map(z=>[z.id,title(z)]),'Без привязки');zoneSelect.value=x.zone_id;zoneSelect.addEventListener('change',()=>mutate(()=>x.zone_id=zoneSelect.value));wrap.append(field('Зона для проверки условий',zoneSelect));
    wrap.append(button('Новый ключевой кадр',()=>{video.pause();drawing.setTool('box',x.id);},'wide'));
    wrap.append(checked('Линейная интерполяция между кадрами',x.interpolate,v=>mutate(()=>x.interpolate=v)));
    const keys=node('div',{class:'keyframes'});for(const k of x.keyframes)keys.append(button(time(k.time),()=>{seek(k.time);setTimeout(renderShapeEditor,100);}));wrap.append(keys);
    const box=boxAt(x,now(),state.current.media.fps);
    if(box){const coords=node('div',{class:'coords'});const inputs=box.map((v,i)=>{const input=node('input',{type:'number',min:0,max:1,step:.001,value:v.toFixed(4),'aria-label':['X','Y','Ширина','Высота'][i]});coords.append(field(['X','Y','Ширина','Высота'][i],input));return input;});wrap.append(coords,button('Применить рамку',()=>{const b=inputs.map(n=>Number(n.value));if(b.some(v=>!Number.isFinite(v)||v<0)||b[2]<=0||b[3]<=0||b[0]+b[2]>1.000001||b[1]+b[3]>1.000001){error('Рамка должна быть внутри кадра, координаты от 0 до 1.');return;}mutate(()=>putKeyframe(x,b));}));
      wrap.append(button('Удалить рамку на этом кадре',()=>mutate(()=>{x.keyframes=x.keyframes.filter(k=>Math.abs(k.time-now())>Math.max(.025,.51/state.current.media.fps));}),'wide danger'));
    }
    wrap.append(button('Зона вокруг объекта',()=>suggestZone(x),'wide'));
    wrap.append(node('p',{class:'muted'},'Интерполяция действует только между ключевыми кадрами и требует проверки. Она не означает присутствие на всём видео.'));
  }
}
function suggestZone(o){
  const b=boxAt(o,now(),state.current.media.fps);if(!b){error('Перейдите к ключевому кадру объекта.');return;}
  const [x,y,w,h]=b,l=Math.max(0,x-.08),t=Math.max(0,y-.08),r=Math.min(1,x+w+.08),bot=Math.min(1,y+h+.08),id=uid('z');
  mutate(a=>{a.zones.push({id,label:o.label==='room_door'?'entrance':'cashier',name:o.label==='room_door'?'Вход':'Рабочая зона',points:[[l,t],[r,t],[r,bot],[l,bot]],anchors:[o.id],confirmed:false});o.zone_id=id;state.selected=id;});
}
function renderTargets(){
  if(!state.a)return;options($('targetId'),[...state.a.zones,...state.a.objects].map(x=>[x.id,`${x.points?'Зона':'Объект'}: ${title(x)}`]),'Не выбрано');
  options($('actorId'),state.a.objects.filter(x=>x.label==='person').map(x=>[x.id,`${title(x)} · ${x.id}`]),'Неизвестный участник');options($('eventZone'),state.a.zones.map(x=>[x.id,title(x)]),'Не выбрана');
}
function seek(t){video.currentTime=Math.max(0,Math.min(Number(t),Math.max(0,duration()-.02)));updateTime();}
function updateTime(){if(!state.current)return;$('clock').textContent=`${time(video.currentTime)} / ${time(duration())}`;$('seek').value=video.currentTime;drawing.render();draftPanel.draw();}
function renderTimeline(){
  const line=$('timeline');line.replaceChildren();for(const [arr,cls]of [[state.a.events,'event-mark'],[state.a.states,'state-mark']])for(const x of arr){const b=button('',()=>{seek(x.start);editInterval(x.id);showTab('intervals');},cls);b.style.left=(x.start/duration()*100)+'%';b.style.width=(Math.max(.002,(x.end-x.start)/duration())*100)+'%';b.title=`${state.config.events[x.label]||state.config.states[x.value]} ${time(x.start)}–${time(x.end)}`;b.setAttribute('aria-label',b.title);line.append(b);}
  episodePanel.timeline(line,duration(),e=>{showTab('episodes');episodePanel.load(e);episodePanel.render();});}
function renderIntervals(){
  const list=$('intervalList');list.replaceChildren();const rows=[...state.a.states.map(x=>({...x,kind:'state'})),...state.a.events.map(x=>({...x,kind:'event'}))].sort((a,b)=>a.start-b.start);
  for(const x of rows){const card=node('div',{class:'item'}),row=node('div',{class:'row'}),b=button('',()=>{seek(x.start);editInterval(x.id);},'item-main');b.append(node('strong',{},state.config.states[x.value]||state.config.events[x.label]),node('small',{},`${time(x.start)}–${time(x.end)} · ${title(target(x.target_id))}`));if(x.note)b.append(node('p',{},x.note));if(x.provenance&&x.provenance!=='human')b.append(node('span',{class:'pill'},x.provenance==='ai_suggestion'?'Источник: ИИ':'Источник: условие'));row.append(b,button('×',()=>{if(state.edit?.id===x.id)resetInterval();mutate(a=>{a.states=a.states.filter(y=>y.id!==x.id);a.events=a.events.filter(y=>y.id!==x.id);});},'delete'));row.lastChild.setAttribute('aria-label','Удалить интервал');card.append(row);list.append(card);}
  if(!rows.length)list.append(node('p',{class:'empty'},'Интервалов пока нет. A и B отмечают начало и конец в текущей позиции плеера.'));
}
function kindChanged(){$('stateFields').hidden=$('intervalKind').value!=='state';$('eventFields').hidden=$('intervalKind').value!=='event';}
function resetInterval(){state.edit=null;$('addInterval').textContent='Добавить интервал';$('cancelInterval').hidden=true;$('intervalKind').disabled=false;$('intervalNote').value='';$('fullyVisible').checked=false;kindChanged();}
function editInterval(id){
  const s=state.a.states.find(x=>x.id===id),e=state.a.events.find(x=>x.id===id),x=s||e;if(!x)return;
  state.edit={id,kind:s?'state':'event',provenance:e?.provenance};$('intervalKind').value=state.edit.kind;$('intervalKind').disabled=true;kindChanged();$('intervalStart').value=x.start;$('intervalEnd').value=x.end;$('targetId').value=x.target_id;$('intervalNote').value=x.note;
  if(s){$('stateValue').value=x.value;$('fullyVisible').checked=x.fully_visible;}else{$('eventLabel').value=x.label;$('actorId').value=x.actor_id;$('eventZone').value=x.zone_id;}
  $('addInterval').textContent='Сохранить интервал';$('cancelInterval').hidden=false;
}
function addInterval(){
  const kind=$('intervalKind').value,start=Number($('intervalStart').value),end=Number($('intervalEnd').value),tid=$('targetId').value;
  if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<start||end>duration()+.05){error('Укажите интервал внутри видео: конец не раньше начала.');return;}
  const x={id:state.edit?.id||uid(kind==='state'?'s':'e'),start,end,note:$('intervalNote').value,target_id:tid};
  if(kind==='state'){
    x.value=$('stateValue').value;x.fully_visible=$('fullyVisible').checked;
    if(!tid){error('Выберите объект или зону для состояния.');return;}
    if(x.value.endsWith('_absent')&&!x.fully_visible){error('Отсутствие можно отметить только когда вся зона видна.');return;}
    if(x.value.startsWith('person_')&&!target(tid)?.points){error('Для присутствия человека выберите зону.');return;}
    if(['open','closed'].includes(x.value)&&!['safe','safe_door','cash_drawer','room_door'].includes(target(tid)?.label)){error('Открыто / закрыто: выберите сейф, ящик или дверь.');return;}
  }else{x.label=$('eventLabel').value;x.actor_id=$('actorId').value;x.zone_id=$('eventZone').value;x.provenance=state.edit?.provenance||'human';}
  mutate(a=>{const arr=kind==='state'?a.states:a.events,i=arr.findIndex(y=>y.id===x.id);if(i>=0)arr[i]=x;else arr.push(x);});resetInterval();error();
}
function mark(which){video.pause();if(state.tab==='episodes'){episodePanel.mark(which,now());$('toolHint').textContent=`${which==='start'?'Начало':'Конец'} эпизода: ${time(now())}`;return;}$(which==='start'?'intervalStart':'intervalEnd').value=now();showTab('intervals');$('toolHint').textContent=`${which==='start'?'Начало':'Конец'} интервала: ${time(now())}`;}
async function checkRules(){await flush();const rows=await api(`api/videos/${state.current.id}/candidates`),p=$('candidates');p.replaceChildren();if(!rows.length)p.append(node('p',{class:'empty'},'Условия не найдены. Проверьте привязку объекта к подтверждённой зоне и пересечение интервалов. Отсутствие результата не означает отсутствие нарушения.'));
  for(const x of rows){const card=node('div',{class:'item condition'});card.append(node('strong',{},state.config.events[x.label]),node('p',{},`${time(x.start)}–${time(x.end)} · ${(x.end-x.start).toFixed(1)} с · ${title(target(x.zone_id))}`),node('p',{class:'muted'},x.note));const exists=()=>state.a.events.some(e=>e.label===x.label&&e.target_id===x.target_id&&e.start===x.start&&e.end===x.end);const b=button(exists()?'Уже добавлено':'Добавить наблюдение',()=>{mutate(a=>a.events.push({id:uid('e'),label:x.label,start:x.start,end:x.end,note:x.note,target_id:x.target_id,zone_id:x.zone_id,actor_id:'',provenance:'rule_candidate'}));showTab('intervals');});b.disabled=exists();card.append(button('Показать',()=>seek(x.start)),b);p.append(card);}}
function downloadDraft(){let data=stamp();if(!state.dirty){try{data=JSON.parse(localStorage.getItem('cash-draft-'+state.current.id))||data;}catch{}}const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=node('a',{href:url,download:`draft-${state.current.filename}.json`});a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
for(const b of document.querySelectorAll('[data-tab]')){b.addEventListener('click',()=>showTab(b.dataset.tab));b.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();const tabs=[...document.querySelectorAll('[data-tab]')],next=tabs[(tabs.indexOf(b)+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length];showTab(next.dataset.tab);next.focus();});}
for(const b of document.querySelectorAll('[data-tool]'))b.addEventListener('click',()=>{video.pause();drawing.setTool(b.dataset.tool);if(b.dataset.tool!=='select')showTab('shapes');});
$('search').addEventListener('input',renderList);$('statusFilter').addEventListener('change',renderList);
$('play').addEventListener('click',()=>video.paused?video.play().catch(e=>error(e.message)):video.pause());video.addEventListener('play',()=>{$('play').textContent='Ⅱ';});video.addEventListener('pause',()=>{$('play').textContent='▶';});
video.addEventListener('timeupdate',updateTime);video.addEventListener('seeked',()=>{updateTime();if(state.tab==='shapes')renderShapeEditor();});video.addEventListener('loadedmetadata',()=>{fitStage();updateTime();});video.addEventListener('canplay',()=>{$('mediaStatus').hidden=true;});video.addEventListener('waiting',()=>{$('mediaStatus').hidden=false;$('mediaStatus').textContent='Загрузка видео…';});video.addEventListener('error',()=>{$('mediaStatus').hidden=false;$('mediaStatus').textContent='Не удалось воспроизвести видео. Проверьте сервер и обновите страницу.';});
$('seek').addEventListener('input',e=>seek(e.target.value));$('speed').addEventListener('change',e=>video.playbackRate=Number(e.target.value));$('backFrame').addEventListener('click',()=>{video.pause();seek(now()-1/state.current.media.fps);});$('forwardFrame').addEventListener('click',()=>{video.pause();seek(now()+1/state.current.media.fps);});$('overlayToggle').addEventListener('change',()=>drawing.render());
$('markStart').addEventListener('click',()=>mark('start'));$('markEnd').addEventListener('click',()=>mark('end'));$('wholeVideo').addEventListener('click',()=>{$('intervalStart').value=0;$('intervalEnd').value=duration().toFixed(3);});$('seekStart').addEventListener('click',()=>seek($('intervalStart').value));$('intervalKind').addEventListener('change',kindChanged);$('addInterval').addEventListener('click',addInterval);$('cancelInterval').addEventListener('click',resetInterval);
$('reviewer').addEventListener('change',()=>{const name=$('reviewer').value;try{localStorage.setItem('cash-reviewer',name);}catch{}mutate(a=>a.reviewer=name);});$('verdict').addEventListener('change',()=>mutate(a=>a.verdict=$('verdict').value));$('fullReview').addEventListener('change',()=>mutate(a=>a.full_video_reviewed=$('fullReview').checked));$('notes').addEventListener('input',()=>mutate(a=>a.notes=$('notes').value));
for(const [id,key]of [['thresholdSafe','safe'],['thresholdCash','cash'],['thresholdDoor','door']])$(id).addEventListener('change',()=>{const n=Number($(id).value);if(!Number.isFinite(n)||n<.1||n>3600){error('Порог должен быть от 0.1 до 3600 секунд.');return;}mutate(a=>a.thresholds[key]=n);});
$('markReviewed').addEventListener('click',async()=>{
  if(!$('reviewer').value.trim()||$('verdict').value==='unset'||!$('fullReview').checked){error('Для завершения укажите имя, вывод и подтвердите просмотр целиком.');return;}
  if(state.a.zones.some(z=>!z.confirmed)){error('Подтвердите или удалите черновые зоны.');showTab('shapes');return;}
  mutate(a=>{a.status='reviewed';a.reviewer=$('reviewer').value;},{review:true});try{await flush();}catch{}
});
$('saveNow').addEventListener('click',()=>flush().catch(()=>{}));$('exportJson').addEventListener('click',async e=>{e.preventDefault();try{await flush();window.location.href='api/export';}catch{}});
$('checkRules').addEventListener('click',()=>checkRules().catch(e=>error(e.message)));
$('aiEvent').addEventListener('click',()=>{
  const ai=state.current.ai,raw=state.current.audit,t=Number(ai.timecode_sec??raw.timecode_sec),start=Number.isFinite(t)?Math.max(0,Math.min(duration(),t)):0,id=uid('e');
  mutate(a=>a.events.push({id,label:'other',start,end:start,note:ai.observed_action||raw.observed_action||'',actor_id:'',target_id:'',zone_id:'',provenance:'ai_suggestion'}));showTab('intervals');editInterval(id);seek(start);
});
$('nextVideo').addEventListener('click',()=>{const i=state.videos.findIndex(x=>x.id===state.current.id),next=state.videos[(i+1)%state.videos.length];openVideo(next.id).catch(e=>error(e.message));});
$('undo').addEventListener('click',()=>{if(!state.history.length)return;state.a=state.history.pop();state.dirty=true;state.serial++;if(state.selected&&!target(state.selected))state.selected=null;backup();$('notes').value=state.a.notes;$('reviewer').value=state.a.reviewer;$('sceneId').value=state.a.scene_id||'';for(const [id,k]of [['thresholdSafe','safe'],['thresholdCash','cash'],['thresholdDoor','door']])$(id).value=state.a.thresholds[k];renderAnnotations();saveStatus('Есть изменения…');clearTimeout(state.timer);state.timer=setTimeout(()=>flush().catch(()=>{}),650);});
$('downloadDraft').addEventListener('click',downloadDraft);$('reloadServer').addEventListener('click',()=>{if(state.dirty)downloadDraft();state.dirty=false;state.conflict=false;clearTimeout(state.timer);openVideo(state.current.id,true).catch(e=>error(e.message));});
$('restoreDraft').addEventListener('click',()=>{try{const draft=JSON.parse(localStorage.getItem('cash-draft-'+state.current.id));if(!draft)return;if(draft.revision!==state.revision){error('Серверная версия уже изменилась. Автоматическое восстановление отключено: скачайте черновик и перенесите нужные изменения.');return;}mutate(()=>state.a=draft.annotation);$('recovery').hidden=true;$('notes').value=state.a.notes;$('reviewer').value=state.a.reviewer;$('sceneId').value=state.a.scene_id||'';renderAnnotations();}catch(e){error(e.message);}});
$('helpButton').addEventListener('click',()=>$('help').showModal());$('closeHelp').addEventListener('click',()=>$('help').close());
window.addEventListener('beforeunload',e=>{if(state.dirty){e.preventDefault();e.returnValue='';}});
document.addEventListener('keydown',e=>{
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();flush().catch(()=>{});return;}
  if(/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)||e.target.isContentEditable||$('help').open)return;
  if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){e.preventDefault();$('undo').click();return;}
  if(e.ctrlKey||e.metaKey||e.altKey||!state.a)return;
  if(e.key===' '&&e.target.tagName!=='BUTTON'){e.preventDefault();$('play').click();}
  else if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();video.pause();seek(now()+(e.key==='ArrowRight'?1:-1)*(e.shiftKey?1/state.current.media.fps:1));}
  else if(e.key==='Escape')drawing.cancel();else if(e.key==='Enter'&&drawing.tool==='zone'){e.preventDefault();drawing.finishZone();}
  else if(e.code==='KeyR')$('boxTool').click();else if(e.code==='KeyZ')$('zoneTool').click();else if(e.code==='KeyV')$('selectTool').click();else if(e.code==='KeyA')mark('start');else if(e.code==='KeyB')mark('end');
});
const episodePanel=new EpisodePanel({annotation:()=>state.a,config:()=>state.config,current:()=>state.current,videos:()=>state.videos,api,mutate,flush,seek,time,duration,error,uid});
const draftPanel=new DraftPanel({api,config:()=>state.config,currentId:()=>state.current?.id,media:()=>state.current?.media,revision:()=>state.revision,tab:()=>state.tab,video,seek,time,error,flush,showTab,reload:()=>openVideo(state.current.id)});
async function boot(){
  [state.config,state.videos]=await Promise.all([api('api/config'),api('api/videos')]);options($('stateValue'),Object.entries(state.config.states));options($('eventLabel'),Object.entries(state.config.events));drawClass('select');episodePanel.setup();renderList();
  if(!state.videos.length){saveStatus('Нет видео');error('В каталоге набора нет доступных видео. Проверьте ANNOTATION_DATASET.');return;}
  const hash=location.hash.slice(1),id=state.videos.find(x=>x.id===hash)?.id||state.videos.find(x=>x.status!=='reviewed')?.id||state.videos[0].id;await openVideo(id);
}
boot().catch(e=>{saveStatus('Ошибка подключения',true);error(e.message);});

$('sceneId').addEventListener('change',()=>mutate(a=>a.scene_id=$('sceneId').value.trim()));
$('copyZones').addEventListener('click',async()=>{
  if(!$('copySource').value||!$('sameView').checked){error('Выберите видео и подтвердите тот же неподвижный ракурс.');return;}
  const currentId=state.current.id,source=$('copySource').value;$('copyZones').disabled=true;
  try{
    const other=await api('api/videos/'+source);if(state.current.id!==currentId)return;
    if(!other.annotation.zones.length){error('В выбранном видео ещё нет зон.');return;}
    const ratio=state.current.media.width/state.current.media.height,otherRatio=other.media.width/other.media.height;
    if(Math.abs(ratio-otherRatio)>.01){error('Соотношение сторон отличается. Нарисуйте зоны на этом видео вручную.');return;}
    mutate(a=>{a.zones.push(...other.annotation.zones.map(z=>({...structuredClone(z),id:uid('z'),anchors:[],confirmed:false})));if(!a.scene_id)a.scene_id=other.annotation.scene_id||'';});
    $('sceneId').value=state.a.scene_id||'';$('sameView').checked=false;error();
  }catch(e){error(e.message);}finally{$('copyZones').disabled=false;}
});

$('exportCoco').addEventListener('click',async e=>{e.preventDefault();try{await flush();window.location.href='api/export/coco';}catch{}});

if (location.pathname.startsWith('/annotate/')) $('mainAppLink').hidden=false;
