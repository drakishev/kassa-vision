// Quick episode protocol: participants and "who does what with which item, when".
// No boxes are needed; geometry tabs stay available for detector training data.
const $=id=>document.getElementById(id);
function node(tag,attrs={},text=''){const n=document.createElement(tag);for(const [k,v] of Object.entries(attrs)){if(k==='class')n.className=v;else n.setAttribute(k,v);}if(text)n.textContent=text;return n;}
function button(text,fn,cls=''){const b=node('button',{type:'button',class:cls},text);b.addEventListener('click',fn);return b;}
function fill(select,entries,empty=null){const previous=select.value;select.replaceChildren();if(empty!==null)select.append(node('option',{value:''},empty));for(const [v,l] of entries)select.append(node('option',{value:v},l));if([...select.options].some(o=>o.value===previous))select.value=previous;}

// Mirrors annotation_service/protocol.py.
export function protocolGaps(a,organizerLabel){
  const gaps=[],episodes=a.episodes||[],status=a.label_check?.status||'unset';
  if(!(a.reviewer||'').trim())gaps.push('Не указан проверяющий');
  if(!a.full_video_reviewed)gaps.push('Видео не просмотрено целиком');
  if(organizerLabel==='нарушение'){
    if(status==='unset')gaps.push('Не отмечено, что объясняет метку организатора');
    else if(['explained','partly'].includes(status)&&!episodes.some(e=>e.explains_label))gaps.push('Нет эпизода с отметкой «объясняет метку»');
  }
  if(!episodes.length)gaps.push('Нет ни одного эпизода (для нормального видео отметьте обычные действия)');
  if(episodes.some(e=>!e.actor_id))gaps.push('У части эпизодов не указано, кто действует');
  if(episodes.some(e=>e.action==='hand_over'&&!e.recipient_id))gaps.push('У передачи не указано, кому');
  return gaps;
}

export class EpisodePanel{
  constructor(deps){this.d=deps;this.edit=null;this.bind();}
  get a(){return this.d.annotation();}
  get config(){return this.d.config();}
  name(id){return (this.a.participants||[]).find(p=>p.id===id)?.name||'';}
  bind(){
    $('labelCheck').addEventListener('change',()=>this.d.mutate(a=>{a.label_check={...a.label_check,status:$('labelCheck').value};}));
    $('labelText').addEventListener('change',()=>this.d.mutate(a=>{a.label_check={...a.label_check,text:$('labelText').value};}));
    $('epAction').addEventListener('change',()=>this.actionChanged());
    $('epAdd').addEventListener('click',()=>this.save());
    $('epCancel').addEventListener('click',()=>this.reset());
    $('epExplains').addEventListener('change',()=>{if($('epExplains').checked)$('epNormal').checked=false;});
    $('epNormal').addEventListener('change',()=>{if($('epNormal').checked)$('epExplains').checked=false;});
    $('episodeGuide').addEventListener('click',e=>{e.preventDefault();$('episodeHelp').showModal();});
    $('closeEpisodeHelp').addEventListener('click',()=>$('episodeHelp').close());
    $('exportEpisodes').addEventListener('click',async e=>{e.preventDefault();try{await this.d.flush();window.location.href='api/export/episodes';}catch{}});
    $('copyEpisodes').addEventListener('click',()=>this.copy().catch(e=>this.d.error(e.message)));
  }
  // Paired recordings repeat one scene; copied episodes are a starting point to re-check.
  async copy(){
    const id=$('copyEpisodeSource').value,offset=Number($('copyEpisodeOffset').value||0),current=this.d.current();
    if(!id){this.d.error('Выберите видео-источник.');return;}
    if(!Number.isFinite(offset)||Math.abs(offset)>120){this.d.error('Сдвиг — число секунд от −120 до 120.');return;}
    const source=await this.d.api('api/videos/'+id);if(this.d.current()?.id!==current.id)return;
    const from=source.annotation;if(!(from.episodes||[]).length){this.d.error('В выбранном видео нет эпизодов.');return;}
    // Identical files share group_id: timing, flags and the label explanation still apply.
    const identical=!!current.ai?.group_id&&current.ai.group_id===source.ai?.group_id&&offset===0,duration=this.d.duration();
    this.d.mutate(a=>{
      const map={};a.participants=a.participants||[];
      for(const p of from.participants||[]){const same=a.participants.find(x=>x.name===p.name&&x.role===p.role);if(same)map[p.id]=same.id;else{const nid=this.d.uid('p');a.participants.push({...p,id:nid});map[p.id]=nid;}}
      const tag=identical?`[копия из ${source.filename}, идентичный файл]`:`[копия из ${source.filename}, сдвиг ${offset>=0?'+':''}${offset} с — проверить время и действие]`;
      for(const e of from.episodes){
        const start=Math.max(0,e.start+offset),end=Math.min(duration,e.end+offset);if(end<start||start>duration)continue;
        a.episodes=[...(a.episodes||[]),{...e,id:this.d.uid('ep'),start,end,actor_id:map[e.actor_id]||'',recipient_id:map[e.recipient_id]||'',
          explains_label:identical&&e.explains_label,hypothesis:identical?e.hypothesis:'',note:`${tag} ${e.note||''}`.trim().slice(0,4000)}];
      }
      if(identical&&(!a.label_check||a.label_check.status==='unset'))a.label_check={...from.label_check};
    });
    $('copyEpisodeSource').value='';this.d.error();
  }
  setup(){
    const c=this.config;
    fill($('labelCheck'),Object.entries(c.label_checks));fill($('epAction'),Object.entries(c.actions));fill($('epItem'),Object.entries(c.items));fill($('epVisibility'),Object.entries(c.visibility));
    const add=$('participantAdd');add.replaceChildren();
    for(const [role,label] of Object.entries(c.roles))add.append(button('+ '+label,()=>this.addParticipant(role)));
  }
  mark(which,t){$(which==='start'?'epStart':'epEnd').value=t.toFixed(2);}
  addParticipant(role){
    const label=this.config.roles[role],same=(this.a.participants||[]).filter(p=>p.role===role).length;
    const name=['cashier','unknown'].includes(role)&&!same?label:`${label} ${same+1}`;
    this.d.mutate(a=>{a.participants=[...(a.participants||[]),{id:this.d.uid('p'),name,role}];});
  }
  removeParticipant(p){
    if((this.a.episodes||[]).some(e=>e.actor_id===p.id||e.recipient_id===p.id)){this.d.error(`«${p.name}» используется в эпизодах. Сначала измените эти эпизоды.`);return;}
    this.d.mutate(a=>{a.participants=a.participants.filter(x=>x.id!==p.id);});
  }
  renameParticipant(p){
    const name=prompt('Условное имя участника (без описания внешности)',p.name);
    if(!name||!name.trim())return;
    this.d.mutate(a=>{a.participants.find(x=>x.id===p.id).name=name.trim().slice(0,60);});
  }
  actionChanged(){const handover=$('epAction').value==='hand_over';$('epRecipient').closest('label').classList.toggle('emphasis',handover);}
  reset(){
    this.edit=null;$('epAdd').textContent='Добавить эпизод';$('epCancel').hidden=true;
    for(const id of ['epHypothesis','epNote'])$(id).value='';$('epExplains').checked=false;$('epNormal').checked=false;$('epVisibility').value='clear';$('epItem').value='unknown';
  }
  load(e){
    this.edit=e.id;$('epStart').value=e.start;$('epEnd').value=e.end;$('epActor').value=e.actor_id;$('epRecipient').value=e.recipient_id;$('epAction').value=e.action;$('epItem').value=e.item;$('epVisibility').value=e.visibility;
    $('epExplains').checked=e.explains_label;$('epNormal').checked=e.hard_negative;$('epHypothesis').value=e.hypothesis;$('epNote').value=e.note;
    $('epAdd').textContent='Сохранить эпизод';$('epCancel').hidden=false;this.actionChanged();this.d.seek(e.start);
  }
  save(){
    const start=Number($('epStart').value),end=Number($('epEnd').value),duration=this.d.duration();
    if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<start||end>duration+.05){this.d.error('Укажите начало и конец внутри видео (клавиши A и B).');return;}
    if(!$('epActor').value){this.d.error('Укажите, кто действует. Если не видно — добавьте участника «Роль неясна».');return;}
    if($('epActor').value===$('epRecipient').value){this.d.error('«Кто» и «кому» должны быть разными участниками.');return;}
    const e={id:this.edit||this.d.uid('ep'),start,end,note:$('epNote').value,action:$('epAction').value,actor_id:$('epActor').value,recipient_id:$('epRecipient').value,
      item:$('epItem').value,visibility:$('epVisibility').value,explains_label:$('epExplains').checked,hard_negative:$('epNormal').checked,hypothesis:$('epHypothesis').value.slice(0,300)};
    this.d.mutate(a=>{const list=a.episodes||[],i=list.findIndex(x=>x.id===e.id);if(i>=0)list[i]=e;else list.push(e);a.episodes=list;});
    this.reset();this.d.error();
  }
  remove(e){this.d.mutate(a=>{a.episodes=a.episodes.filter(x=>x.id!==e.id);});if(this.edit===e.id)this.reset();}
  summary(e){
    const c=this.config,who=this.name(e.actor_id)||'?',to=e.recipient_id?` → ${this.name(e.recipient_id)}`:'',item=e.item&&!['none','unknown'].includes(e.item)?` · ${c.items[e.item]}`:e.item==='unknown'?' · предмет не разобрать':'';
    return `${who}: ${c.actions[e.action]}${item}${to}`;
  }
  render(){
    const a=this.a,c=this.config,current=this.d.current();if(!a||!c||!current)return;
    const source=current.source||{},info=$('labelInfo');info.replaceChildren();
    const positive=source.label==='нарушение',t=Number(source.timecode_sec);
    info.append(node('p',{},positive?`Организатор: нарушение, ${source.timecode_raw||source.timecode_sec+' с'}. Тип не указан — его нужно понять по видео.`:`Организатор: ${source.label||'метка неизвестна'}. Отметьте обычные действия и похожие на нарушение нормальные случаи.`));
    if(positive&&Number.isFinite(t))info.append(button(`↗ Перейти к ${t} с (смотрите с ${Math.max(0,t-10)} с)`,()=>this.d.seek(Math.max(0,t-10)),'wide'));
    $('labelCheck').value=a.label_check?.status||'unset';$('labelText').value=a.label_check?.text||'';const askLabel=positive||(a.label_check?.status&&a.label_check.status!=='unset');
    for(const id of ['labelCheck','labelText'])$(id).closest('label').hidden=!askLabel;
    const people=a.participants||[],list=$('participantList');list.replaceChildren();
    for(const p of people){const chip=node('span',{class:'chip'});chip.append(button(p.name,()=>this.renameParticipant(p),'chip-name'),button('×',()=>this.removeParticipant(p),'chip-x'));chip.lastChild.setAttribute('aria-label',`Удалить ${p.name}`);list.append(chip);}
    if(!people.length)list.append(node('p',{class:'empty'},'Добавьте всех, кто участвует: обычно кассир и клиенты.'));
    const entries=people.map(p=>[p.id,p.name]);fill($('epActor'),entries,'— выберите —');fill($('epRecipient'),entries,'— никому —');
    const rows=[...(a.episodes||[])].sort((x,y)=>x.start-y.start),box=$('episodeList');box.replaceChildren();
    for(const e of rows){
      const card=node('div',{class:'item'+(this.edit===e.id?' selected':'')}),row=node('div',{class:'row'}),main=button('',()=>this.load(e),'item-main');
      main.append(node('strong',{},this.summary(e)),node('small',{},`${this.d.time(e.start)}–${this.d.time(e.end)} · ${c.visibility[e.visibility]}`));
      if(e.explains_label)main.append(node('span',{class:'pill explains'},'объясняет метку'));
      if(e.hard_negative)main.append(node('span',{class:'pill normal'},'нормальное, похоже на нарушение'));
      if(e.hypothesis)main.append(node('p',{},'Гипотеза: '+e.hypothesis));
      if(e.note)main.append(node('p',{class:'muted'},e.note));
      row.append(main,button('×',()=>this.remove(e),'delete'));row.lastChild.setAttribute('aria-label','Удалить эпизод');card.append(row);box.append(card);
    }
    if(!rows.length)box.append(node('p',{class:'empty'},'Эпизодов пока нет. Поставьте паузу в начале действия (A), в конце (B), выберите кто/что/кому и добавьте.'));
    fill($('copyEpisodeSource'),(this.d.videos()||[]).filter(v=>v.id!==current.id).map(v=>[v.id,v.filename]),'Выберите видео');
    const gaps=protocolGaps(a,source.label),g=$('protocolGaps');g.replaceChildren();
    if(!gaps.length)g.append(node('li',{class:'ok'},'Всё заполнено: видео войдёт в выгрузку эпизодов как полное.'));
    for(const x of gaps)g.append(node('li',{},x));
    $('tab-episodes').dataset.complete=gaps.length?'false':'true';
  }
  timeline(line,duration,onClick){
    for(const e of this.a?.episodes||[]){
      const b=button('',()=>onClick(e),'episode-mark'+(e.explains_label?' explains':''));
      b.style.left=(e.start/duration*100)+'%';b.style.width=(Math.max(.004,(e.end-e.start)/duration)*100)+'%';
      b.title=`${this.summary(e)} ${this.d.time(e.start)}–${this.d.time(e.end)}`;b.setAttribute('aria-label',b.title);line.append(b);
    }
  }
}
