const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert=require('node:assert/strict');
const BASE=process.env.ANNOTATION_TEST_URL;
const OUT=process.env.ANNOTATION_TEST_OUTPUT;
if(!BASE || !OUT) throw new Error('Run through python -m annotation_service.tests.run_browser');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE,args:['--no-sandbox']});
 const ctx=await browser.newContext({viewport:{width:1440,height:1000}});const page=await ctx.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')console.log('CONSOLE',m.text().slice(0,180));});
 if(new URL(BASE).pathname.startsWith('/annotate')){
   const login=await ctx.request.post(new URL(BASE).origin+'/ui/api/auth/login',{data:{username:'tester',password:'synthetic-password-42'}});
   assert.equal(login.status(),200);
 }
 await page.goto(BASE);await page.waitForFunction(()=>document.querySelector('#video').readyState>=2);console.log('video playable');
 await page.click('#videoName');await page.keyboard.press('Shift+ArrowRight');await page.waitForFunction(()=>document.querySelector('#video').currentTime>0);await page.keyboard.press('ArrowLeft');await page.waitForFunction(()=>document.querySelector('#video').currentTime===0);await page.keyboard.press('KeyA');await page.keyboard.press('KeyB');assert.equal(await page.locator('#epStart').inputValue(),'0.00');assert.equal(await page.locator('#epEnd').inputValue(),'0.00');await page.click('#tab-intervals');await page.keyboard.press('KeyA');await page.keyboard.press('KeyB');assert.equal(await page.locator('#intervalStart').inputValue(),'0');assert.equal(await page.locator('#intervalEnd').inputValue(),'0');await page.click('#tab-ai');
 assert.equal(await page.locator('#aiContent img').count(),0);assert.match(await page.locator('#aiContent').innerText(),/<img/);
 async function saved(){await page.waitForFunction(()=>document.querySelector('#saveStatus').textContent.startsWith('Сохранено'));}
 async function drawBox(x1,y1,x2,y2){const r=await page.locator('#overlay').boundingBox();await page.mouse.move(r.x+x1*r.width,r.y+y1*r.height);await page.mouse.down();await page.mouse.move(r.x+x2*r.width,r.y+y2*r.height,{steps:8});await page.mouse.up();}
 await page.click('#boxTool');await page.selectOption('#drawClass','safe');await drawBox(.07,.22,.31,.86);await saved();
 assert.equal(await page.locator('#shapeList .item').count(),1);console.log('box created');
 await page.getByRole('button',{name:'Зона вокруг объекта',exact:true}).click();await page.getByLabel('Зона проверена вручную').check();await saved();
 let id=(await page.request.get(BASE+'/api/videos').then(r=>r.json()))[0].id;
 let d=await page.request.get(BASE+'/api/videos/'+id).then(r=>r.json());const oid=d.annotation.objects[0].id,zid=d.annotation.zones[0].id;assert.equal(d.annotation.zones[0].confirmed,true);
 await page.click('#tab-intervals');await page.selectOption('#targetId',oid);await page.selectOption('#stateValue','open');await page.click('#wholeVideo');await page.click('#addInterval');
 await page.selectOption('#targetId',zid);await page.selectOption('#stateValue','person_absent');await page.click('#addInterval');assert.match(await page.locator('#error').innerText(),/вся зона видна/);await page.check('#fullyVisible');await page.click('#addInterval');await saved();
 await page.click('#tab-rules');await page.click('#checkRules');await page.getByRole('button',{name:'Добавить наблюдение',exact:true}).waitFor();await page.getByRole('button',{name:'Добавить наблюдение',exact:true}).click();await saved();console.log('states and rule candidate');
 // Same tracked object on another keyframe.
 await page.click('#tab-shapes');await page.locator('#shapeList .item-main').filter({hasText:'Сейф'}).click();
 await page.locator('#seek').fill('8');await page.locator('#seek').dispatchEvent('input');await page.waitForTimeout(200);
 await page.getByRole('button',{name:'Новый ключевой кадр',exact:true}).click();await drawBox(.10,.24,.34,.86);await saved();
 await page.getByLabel('Линейная интерполяция между кадрами').check();await saved();
 d=await page.request.get(BASE+'/api/videos/'+id).then(r=>r.json());assert.equal(d.annotation.objects.length,1);assert.equal(d.annotation.objects[0].keyframes.length,2);
 // Manual polygon + editing a vertex.
 await page.click('#zoneTool');await page.selectOption('#drawClass','customer');let r=await page.locator('#overlay').boundingBox();for(const [x,y]of [[.55,.2],[.9,.2],[.9,.8],[.55,.8]])await page.mouse.click(r.x+x*r.width,r.y+y*r.height);await page.keyboard.press('Enter');await saved();
 const handle=page.locator('#overlay circle[data-point="0"]');let h=await handle.boundingBox();await page.mouse.move(h.x+h.width/2,h.y+h.height/2);await page.mouse.down();await page.mouse.move(h.x-15,h.y+10);await page.mouse.up();await saved();
 // AI observation remains a distinct provenance.
 await page.click('#tab-ai');await page.click('#aiEvent');await page.selectOption('#eventLabel','other');await page.fill('#intervalEnd','12');await page.click('#addInterval');await saved();
 await page.fill('#reviewer','QA synthetic');await page.locator('#reviewer').blur();await page.selectOption('#verdict','uncertain');await page.check('#fullReview');await page.click('#markReviewed');await saved();
 assert.equal(await page.locator('#markReviewed').isDisabled(),true);await page.reload();await page.waitForFunction(()=>document.querySelector('#video').readyState>=2);assert.equal(await page.locator('#markReviewed').isDisabled(),true);console.log('review survives reload');
 const exp=await page.request.get(BASE+'/api/export/coco').then(r=>r.json());assert.equal(exp.annotations.length,2);assert.equal(exp.images.length,2);
 await page.click('#nextVideo');await page.waitForFunction(()=>document.querySelector('#videoName').textContent==='demo-2.mp4');assert.equal(await page.locator('#shapeList .item').count(),0);await page.locator('[data-video-id="'+id+'"]').click();await page.waitForFunction(()=>document.querySelector('#videoName').textContent==='demo-1.mp4');
 // Two-tab optimistic concurrency: stale writes cannot overwrite another reviewer.
 const other=await ctx.newPage();await other.goto(BASE+'/#'+id);await other.waitForFunction(()=>document.querySelector('#videoName').textContent==='demo-1.mp4');
 await page.fill('#notes','version from first window');await saved();await other.fill('#notes','stale second window draft');await other.waitForFunction(()=>document.querySelector('#error').textContent.includes('другом окне'));
 const persisted=await page.request.get(BASE+'/api/videos/'+id).then(r=>r.json());assert.equal(persisted.annotation.notes,'version from first window');console.log('conflict protected');
 await other.close();await page.click('#tab-shapes');
 for(const w of [1440,1024,768,390,320]){await page.setViewportSize({width:w,height:1000});await page.waitForTimeout(100);const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);assert.equal(overflow,false,'horizontal overflow at '+w);if(w===1440||w===390)await page.screenshot({path:OUT+'/ui-'+w+'.png',fullPage:true});}

 await page.setViewportSize({width:1440,height:1000});
 const rows=await page.request.get(BASE+'/api/videos').then(r=>r.json());
 await page.locator('[data-video-id="'+rows[1].id+'"]').click();
 await page.waitForFunction(()=>document.querySelector('#videoName').textContent==='demo-2.mp4');
 await page.click('#tab-shapes');await page.getByText('Перенести зоны из другого видео',{exact:true}).click();
 await page.selectOption('#copySource',rows[0].id);await page.check('#sameView');await page.click('#copyZones');
 await page.waitForFunction(()=>document.querySelectorAll('#shapeList .item').length===2);await saved();
 const copied=await page.request.get(BASE+'/api/videos/'+rows[1].id).then(r=>r.json());
 assert(copied.annotation.zones.every(z=>!z.confirmed&&z.anchors.length===0));assert.equal(copied.annotation.objects.length,0);
 await page.fill('#sceneId','camera-test');await page.locator('#sceneId').blur();await saved();
 const scene=await page.request.get(BASE+'/api/videos/'+rows[1].id).then(r=>r.json());assert.equal(scene.annotation.scene_id,'camera-test');console.log('copy zones and scene ID: OK');

 await page.click('#tab-draft');await page.waitForFunction(()=>document.querySelector('#draftSummary').textContent.includes('Черновик готов'));
 const objectCard=page.locator('#draftItems .draft-card').filter({has:page.getByRole('button',{name:/^Сейф/})});
 await objectCard.getByRole('button',{name:'Проверить / принять',exact:true}).click();
 await page.locator('#draftDialog').getByLabel('Название',{exact:true}).fill('Сейф проверен');
 await page.waitForFunction(()=>{const im=document.querySelector('#draftEditImage');return im.complete&&im.naturalWidth>0;});
 await page.screenshot({path:OUT+'/draft-review.png',fullPage:true});
 const draftBox=await page.locator('#draftEditOverlay').boundingBox();
 await page.mouse.move(draftBox.x+draftBox.width*.2,draftBox.y+draftBox.height*.25);await page.mouse.down();
 await page.mouse.move(draftBox.x+draftBox.width*.23,draftBox.y+draftBox.height*.27);await page.mouse.up();
 await page.click('#draftAccept');await page.waitForFunction(()=>document.querySelector('#draftEditError').textContent.includes('границы'));
 await page.check('#confirmDraftZones');await page.click('#draftAccept');await page.waitForFunction(()=>!document.querySelector('#draftDialog').open);
 await page.waitForFunction(()=>document.querySelector('#saveStatus').textContent.startsWith('Сохранено'));
 const accepted=await page.request.get(BASE+'/api/videos/'+rows[1].id).then(r=>r.json());assert(accepted.annotation.objects.some(o=>o.name==='Сейф проверен'));assert.equal(accepted.annotation.zones.length,3);
 const corrected=accepted.annotation.objects.find(o=>o.name==='Сейф проверен');assert(corrected.keyframes[0].box[0]>.12);assert.equal(corrected.keyframes[0].polygons.length,0);
 await page.click('#tab-draft');await page.waitForFunction(()=>document.querySelector('#draftSummary').textContent.includes('Черновик готов'));
 const absentCard=page.locator('#draftItems .draft-card').filter({has:page.getByRole('button',{name:/^Человека нет/})});
 await absentCard.getByRole('button',{name:'Проверить / принять',exact:true}).click();await page.click('#draftAccept');await page.waitForFunction(()=>document.querySelector('#draftEditError').textContent.includes('видимость'));
 await page.check('#confirmDraftVisibility');await page.click('#draftAccept');await page.waitForFunction(()=>!document.querySelector('#draftDialog').open);
 await page.waitForTimeout(200);await page.click('#tab-draft');
 const openCard=page.locator('#draftItems .draft-card').filter({has:page.getByRole('button',{name:/^Открыто/})});await openCard.getByRole('button',{name:'Отклонить',exact:true}).click();
 await page.waitForFunction(()=>!Array.from(document.querySelectorAll('#draftItems .item-main')).some(n=>n.textContent.startsWith('Открыто')));
 console.log('draft mask preview, corrected acceptance, dependencies, explicit visibility and rejection: OK');
 if(process.env.AXE_MODULE){
   const AxeBuilder=require(process.env.AXE_MODULE).default;
   for(const tab of ['episodes','ai','draft','shapes','intervals','rules']){
     await page.click('#tab-'+tab);const result=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa']).analyze();
     const violations=result.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)}));
     assert.deepEqual(violations,[],`Accessibility in ${tab}: ${JSON.stringify(violations)}`);
   }
   console.log('axe accessibility across six tabs: OK');
 }

 // Quick episode protocol: participants, one hand-over, gaps and export, without boxes.
 await page.locator('[data-video-id="'+rows[0].id+'"]').click();await page.waitForFunction(()=>document.querySelector('#videoName').textContent==='demo-1.mp4');
 await page.click('#tab-episodes');await page.getByRole('button',{name:'+ Кассир',exact:true}).click();await page.getByRole('button',{name:'+ Клиент',exact:true}).click();await saved();
 await page.locator('#seek').fill('5');await page.locator('#seek').dispatchEvent('input');await page.click('#tab-episodes');await page.keyboard.press('KeyA');
 await page.locator('#seek').fill('7');await page.locator('#seek').dispatchEvent('input');await page.click('#tab-episodes');await page.keyboard.press('KeyB');
 await page.click('#epAdd');assert.match(await page.locator('#error').innerText(),/кто действует/);
 await page.selectOption('#epActor',{label:'Клиент 1'});await page.selectOption('#epAction','hand_over');await page.selectOption('#epItem','cash');await page.selectOption('#epRecipient',{label:'Кассир'});
 await page.check('#epNormal');await page.click('#epAdd');await saved();
 assert.match(await page.locator('#episodeList').innerText(),/Клиент 1: Передать из рук в руки · Деньги → Кассир/);
 assert.equal(await page.locator('#timeline .episode-mark').count(),1);
 const episodes=await page.request.get(BASE+'/api/export/episodes').then(r=>r.json());const ep=episodes.videos.find(v=>v.video_id===rows[0].id).episodes[0];
 assert.deepEqual([ep.actor,ep.recipient,ep.item,ep.hard_negative,Math.round(ep.start),Math.round(ep.end)],['Клиент 1','Кассир','cash',true,5,7]);
 await page.locator('#episodeList .item-main').first().click();assert.equal(await page.locator('#epAdd').innerText(),'Сохранить эпизод');await page.click('#epCancel');
 await page.screenshot({path:OUT+'/episodes-panel.png',fullPage:true});
 await page.click('#episodeGuide');await page.waitForFunction(()=>document.querySelector('#episodeHelp').open);await page.screenshot({path:OUT+'/episodes-guide.png'});await page.click('#closeEpisodeHelp');
 console.log('episode protocol, timeline and export: OK');
 // Copy episodes into the paired recording with a time shift.
 await page.locator('[data-video-id="'+rows[1].id+'"]').click();await page.waitForFunction(()=>document.querySelector('#videoName').textContent==='demo-2.mp4');
 await page.click('#tab-episodes');await page.getByText('Скопировать эпизоды из парного ролика',{exact:true}).click();
 await page.selectOption('#copyEpisodeSource',rows[0].id);await page.fill('#copyEpisodeOffset','1');await page.click('#copyEpisodes');
 // Copying fetches the source first; wait for the copied card before waiting for the save.
 await page.waitForFunction(()=>document.querySelector('#episodeList').textContent.includes('Клиент 1'));await saved();
 const copied2=await page.request.get(BASE+'/api/videos/'+rows[1].id).then(r=>r.json());const ce=copied2.annotation.episodes[0];
 assert.deepEqual([copied2.annotation.episodes.length,Math.round(ce.start),ce.explains_label,copied2.annotation.participants.length],[1,6,false,2]);
 assert.match(ce.note,/копия из demo-1\.mp4, сдвиг \+1 с/);console.log('copy episodes between paired videos: OK');
 assert.deepEqual(errors,[]);console.log('responsive widths and JS errors: OK');await browser.close();
})().catch(e=>{console.error(e);process.exit(1);});
