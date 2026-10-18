// Entirely synthetic mocked API and SVG fixture. No bank media is read.
import {chromium,expect} from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import fs from 'node:fs/promises'
const origin=process.env.DIAGNOSTIC_UI_ORIGIN||'http://127.0.0.1:18789'
const executablePath=process.env.CHROMIUM_PATH
const browser=await chromium.launch({...(executablePath?{executablePath}:{}),headless:true,args:['--no-sandbox']})
const context=await browser.newContext({viewport:{width:1440,height:1000}})
const page=await context.newPage()
const errors=[];page.on('pageerror',error=>errors.push(error.message))
let diagnosticsCalls=0,analysisCalls=0,unavailable=false,legacy=false,lowConfidence=false
const pose=Array.from({length:17},(_,i)=>[200+(i%2)*30,80+i*10,.9])
const metadata={job_id:'synthetic',pipeline:'fusion-v1',frames:[{frame_id:'f0000',time_sec:0,width:640,height:360,detections:[{label:'person',score:.8,box_xyxy:[180,40,270,330],track_id:'person:1'}],poses:[{track_id:'person:1',keypoints:pose}],states:[{label:'safe',value:'closed',box_xyxy:[30,30,130,280],calibrated:false}]},{frame_id:'f0001',time_sec:1,width:640,height:360,detections:[],poses:[]}],scene:{anchor_time_sec:0,masks:[{object_id:'o1',polygons:[[[.1,.1],[.4,.1],[.4,.7],[.1,.7]]],source:'sam2.1',scope:'anchor_frame_only'}],objects:[{id:'o1',label:'table'}]}}
metadata.frames.push({frame_id:'portrait',time_sec:2,width:1080,height:1920,detections:[{label:'person',score:.9,box_xyxy:[300,200,800,1600],track_id:'person:2'}],poses:[{track_id:'person:2',keypoints:pose.map(([x,y,score])=>[x*2,y*4,score])}]})
await fs.mkdir('tests/results',{recursive:true})
await page.route('**/ui/api/**',async route=>{
 const url=new URL(route.request().url());let body
 if(url.pathname.endsWith('/auth/me'))body={user:{id:'synthetic-user',username:'synthetic'}}
 else if(url.pathname.endsWith('/config'))body={ready:true,max_upload_bytes:1000000,max_duration_sec:180}
 else if(url.pathname.endsWith('/diagnostics')){
  diagnosticsCalls++;if(unavailable)return route.fulfill({status:404,json:{error:{message:'unavailable'}}})
  body=legacy?{job_id:'synthetic',pipeline:'legacy-supported',frames:metadata.frames.map(({states,...frame})=>({...frame,poses:frame.poses.map(p=>({...p,keypoints:[...p.keypoints,...pose]}))}))}:metadata
  if(lowConfidence)body={job_id:'synthetic',pipeline:'legacy-supported',frames:[{...metadata.frames[0],states:undefined,detections:[{label:'person',score:.39,box_xyxy:[180,40,270,330],track_id:'person:1'},{label:'person',score:.28,box_xyxy:[400,80,450,230],track_id:'person:2'}],poses:[{track_id:'person:1',keypoints:pose},{track_id:'person:2',keypoints:pose.map(([x,y])=>[x+200,y,.99])}]}]}
 }
 else if(/\/diagnostics\/frames\/\d+$/.test(url.pathname)){
  const frame=metadata.frames[Number(url.pathname.split('/').at(-1))]
  return route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="${frame.width}" height="${frame.height}"><rect width="100%" height="100%" fill="#172d20"/><rect x="30" y="30" width="100" height="250" fill="#65756b"/></svg>`})
 }
 else if(url.pathname.endsWith('/analyze')){analysisCalls++;body={}}
 else body={job_id:'synthetic',filename:'synthetic.mov',status:'done',verdict:'no_violation',violations:[]}
 return route.fulfill({json:body})
})
for(const path of ['/','/v2']){
 await page.goto(origin+path+'?job=synthetic')
 await expect(page.getByRole('link',{name:/Minecraft|Версия 1/})).toHaveCount(0)
 await expect(page.getByRole('link',{name:'Наблюдения · пилот',exact:true})).toHaveCount(0)
 await expect(page.getByRole('link',{name:'Основной режим',exact:true})).toHaveCount(0)
 await expect(page.locator('html')).not.toHaveAttribute('data-design','voxel')
 if(path==='/v2')await expect(page).toHaveURL(origin+'/?job=synthetic')
 const trigger=page.getByRole('button',{name:'Показать работу моделей'})
 const panel=page.getByRole('dialog',{name:'Диагностика моделей'})
 await expect(trigger).toBeVisible()
 await expect(panel).toHaveCount(0)
 const before=diagnosticsCalls
 await trigger.click()
 await expect(panel).toBeVisible()
 await expect(panel.locator('img')).toBeVisible()
 await expect(panel.locator('svg rect')).toHaveCount(1)
 await panel.getByRole('slider').fill('0.95')
 await expect(panel.locator('svg rect')).toHaveCount(0)
 await panel.getByRole('slider').fill('0.45')
 await panel.getByRole('button',{name:'Поза',exact:true}).click()
 await expect(panel.locator('svg circle')).toHaveCount(17)
 await expect(panel.locator('svg line')).toHaveCount(16)
 await panel.getByRole('button',{name:'Сегментация',exact:true}).click()
 await expect(panel.locator('svg polygon')).toHaveCount(1)
 await panel.getByRole('combobox').selectOption('1')
 await expect(panel.locator('svg polygon')).toHaveCount(0)
 await expect(panel.getByText('В этом кадре сегментация не выполнялась.',{exact:true})).toBeVisible()
 await panel.getByRole('button',{name:'Открыть опорный кадр'}).click()
 await expect(panel.locator('svg polygon')).toHaveCount(1)
 await panel.getByRole('button',{name:'Состояния',exact:true}).click()
 await expect(panel.locator('svg text')).toHaveText('safe · closed')
 await panel.getByRole('combobox').selectOption('1')
 await expect(panel.getByText('Этот слой не сохранён для выбранного кадра.',{exact:true})).toBeVisible()
 await expect(panel.locator('svg text')).toHaveCount(0)
 await panel.getByRole('button',{name:'Детекция',exact:true}).click()
 await expect(panel.getByText(/Модель выполнена: детекций нет/)).toBeVisible()
 await panel.getByRole('button',{name:'Исходный кадр',exact:true}).click()
 await expect(panel.locator('svg rect,svg polygon,svg circle,svg line')).toHaveCount(0)
 await panel.getByRole('button',{name:'Детекция',exact:true}).click()
 for(const selected of ['0','2']){
  await panel.getByRole('combobox').selectOption(selected)
  await expect(panel.locator('svg rect')).toHaveCount(1)
  for(const [width,height] of [[1440,1000],[1024,768],[768,900],[375,667],[320,568],[844,390]]){
   await page.setViewportSize({width,height})
   await panel.locator('.diagnostic-image').scrollIntoViewIfNeeded()
   await expect.poll(async()=>panel.evaluate(el=>{
    const dialog=el.getBoundingClientRect()
    const image=el.querySelector('.diagnostic-image img').getBoundingClientRect()
    const svg=el.querySelector('.diagnostic-overlay').getBoundingClientRect()
    return dialog.left>=0&&dialog.top>=0&&dialog.right<=innerWidth+.5&&dialog.bottom<=innerHeight+.5
     &&el.scrollWidth<=el.clientWidth+1&&image.height<=Math.min(innerHeight*.56,600)+1
     &&Math.abs(image.x-svg.x)<1&&Math.abs(image.y-svg.y)<1&&Math.abs(image.width-svg.width)<1&&Math.abs(image.height-svg.height)<1
   })).toBe(true)
   const frame=metadata.frames[Number(selected)]
   await expect.poll(async()=>panel.evaluate((el,frame)=>{
    // Read both rectangles in one browser task, including during resize animations.
    const box=el.querySelector('svg rect').getBoundingClientRect()
    const image=el.querySelector('.diagnostic-image img').getBoundingClientRect()
    const [x,y,u,v]=frame.detections[0].box_xyxy
    return Math.max(...[box.x-image.x-x/frame.width*image.width,box.y-image.y-y/frame.height*image.height,box.width-(u-x)/frame.width*image.width,box.height-(v-y)/frame.height*image.height].map(Math.abs))
   },frame)).toBeLessThan(1)
   const image=await panel.locator('.diagnostic-image img').boundingBox()
   expect(Math.abs(image.width/image.height-frame.width/frame.height)).toBeLessThan(.01)
  }
 }
 await page.setViewportSize({width:1440,height:1000})
 await panel.getByRole('button',{name:'Поза',exact:true}).click()
 await expect.poll(async()=>Math.round((await panel.locator('svg circle').first().boundingBox()).width)).toBe(7)  // 6 px joint + 1 px outline.
 if(path==='/')await page.screenshot({path:'tests/results/diagnostics-dialog-desktop.png'})
 await page.setViewportSize({width:375,height:667})
 await panel.locator('.diagnostic-image').scrollIntoViewIfNeeded()
 await expect.poll(async()=>Math.round((await panel.locator('svg circle').first().boundingBox()).width)).toBe(7)  // 6 px joint + 1 px outline.
 if(path==='/')await page.screenshot({path:'tests/results/diagnostics-dialog-mobile.png'})
 const axe=await new AxeBuilder({page}).include('.model-diagnostics-dialog').analyze()
 if(axe.violations.length)throw Error(JSON.stringify(axe.violations.map(v=>v.id)))
 for(let step=0;step<20;step++){
  await page.keyboard.press('Tab')
  expect(await panel.evaluate(el=>el.contains(document.activeElement))).toBe(true)
 }
 await page.keyboard.press('Escape')
 await expect(panel).toHaveCount(0)
 await expect(trigger).toBeFocused()
 await trigger.click()
 await expect(panel.locator('img')).toBeVisible()
 if(diagnosticsCalls-before!==1)throw Error('Layer switch or reopening fetched diagnostics again')
 await panel.getByRole('button',{name:'Закрыть',exact:true}).click()
 await expect(panel).toHaveCount(0)
 await expect(trigger).toBeFocused()
 await trigger.click()
 await expect(panel).toBeVisible()
 await page.locator('[data-slot="dialog-overlay"]').click({position:{x:2,y:2}})
 await expect(panel).toHaveCount(0)
 await expect(trigger).toBeFocused()
}
legacy=true
await page.goto(origin+'/?job=synthetic')
const legacyPanel=page.getByRole('dialog',{name:'Диагностика моделей'})
await page.getByRole('button',{name:'Показать работу моделей'}).click()
await expect(legacyPanel.getByText('Grounding DINO · ByteTrack',{exact:true})).toBeVisible()
await legacyPanel.getByRole('button',{name:'Поза',exact:true}).click()
await expect(legacyPanel.getByText('RTMW · 17 точек тела',{exact:true})).toBeVisible()
await expect(legacyPanel.locator('svg circle')).toHaveCount(17)
await expect(legacyPanel.locator('svg line')).toHaveCount(16)
for(const layer of ['Сегментация','Состояния']){
 await expect(legacyPanel.getByRole('button',{name:layer,exact:true})).toHaveCount(0)
}
await expect(legacyPanel.getByText('В этой проверке нет сохранённых слоёв: сегментация, состояния.',{exact:true})).toBeVisible()
await expect(legacyPanel.getByText(/SigLIP2|SAM 2.1|Маски вычислены/)).toHaveCount(0)
lowConfidence=true
await page.goto(origin+'/?job=synthetic')
await page.getByRole('button',{name:'Показать работу моделей'}).click()
await expect(legacyPanel.locator('svg rect')).toHaveCount(2)
await legacyPanel.getByRole('slider',{name:/Порог детектора/}).fill('0.45')
await expect(legacyPanel.locator('svg rect')).toHaveCount(0)
await expect(legacyPanel.getByText(/Все детекции скрыты порогом/)).toBeVisible()
await legacyPanel.getByRole('button',{name:'Показать все детекции',exact:true}).click()
await expect(legacyPanel.locator('svg rect')).toHaveCount(2)
await legacyPanel.getByRole('button',{name:'Поза',exact:true}).click()
await expect(legacyPanel.locator('svg circle')).toHaveCount(34)
await expect(legacyPanel.getByRole('slider',{name:/Порог точек позы/})).toHaveValue('0.45')
await legacyPanel.getByRole('slider',{name:/Порог детектора/}).fill('0.35')
await expect(legacyPanel.locator('svg circle')).toHaveCount(17)
await expect(legacyPanel.getByRole('slider',{name:/Порог точек позы/})).toHaveValue('0.45')
await legacyPanel.getByRole('button',{name:'Детекция',exact:true}).click()
await expect(legacyPanel.locator('svg rect')).toHaveCount(1)
await legacyPanel.getByRole('button',{name:'Поза',exact:true}).click()
await legacyPanel.getByRole('slider',{name:/Порог точек позы/}).fill('1')
await expect(legacyPanel.locator('svg circle')).toHaveCount(0)
await legacyPanel.getByRole('slider',{name:/Порог точек позы/}).fill('0.45')
await legacyPanel.getByRole('button',{name:'Показать все детекции',exact:true}).click()
await expect(legacyPanel.locator('svg circle')).toHaveCount(34)
unavailable=true
await page.goto(origin+'/?job=synthetic')
await page.getByRole('button',{name:'Показать работу моделей'}).click()
await expect(page.getByText(/Для этой проверки измерения моделей не сохранены/)).toBeVisible()
if(analysisCalls)throw Error('Diagnostic inspection launched inference')
if(errors.length)throw Error(JSON.stringify(errors))
const result={synthetic_only:true,modal:true,viewport_fit:true,portrait_and_landscape:true,overlay_alignment:true,readable_pose_joints:true,escape_close:true,backdrop_close:true,focus_trapped_and_restored:true,cached_reopen:true,paths:['/','/v2'],layers_tested:5,legacy_supported_names:true,legacy_pose_body17:true,legacy_missing_layers:true,pilot_navigation_hidden:true,confidence_filter:true,low_score_detections_visible_by_default:true,independent_joint_threshold:true,shared_detection_pose_filter:true,reset_threshold:true,anchor_only_segmentation:true,no_stale_overlays:true,empty_vs_missing:true,unavailable:true,widths:[320,375,768,844,1024,1440],axe_violations:0,console_errors:0,analysis_calls:0}
await fs.mkdir('tests/results',{recursive:true});await fs.writeFile('tests/results/model-diagnostics.json',JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));await browser.close()
