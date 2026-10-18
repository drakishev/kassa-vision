// Synthetic account only. Documents are public legal snapshots; no bank media or annotations.
import {chromium,expect} from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import {createHash} from 'node:crypto'
import fs from 'node:fs/promises'

const origin=process.env.LEGAL_UI_ORIGIN||'http://127.0.0.1:18789'
const catalog=JSON.parse(await fs.readFile('public/assets/legal-documents/catalog.json','utf8')).documents
const browser=await chromium.launch({...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),headless:true,args:['--no-sandbox']})
try {
 const context=await browser.newContext({viewport:{width:1440,height:1000}})
 const page=await context.newPage()
 const errors=[];page.on('pageerror',error=>errors.push(error.message))
 let authenticated=false,analysisCalls=0,jobCalls=0,snapshotRequests=0
 page.on('request',request=>{if(/\/assets\/legal-documents\/.*\.txt$/.test(new URL(request.url()).pathname))snapshotRequests++})
 await page.route('**/ui/api/**',async route=>{
  const path=new URL(route.request().url()).pathname
  if(path.endsWith('/auth/login')){authenticated=true;return route.fulfill({json:{user:{id:'synthetic-jury',username:'jury'}}})}
  if(path.endsWith('/auth/me'))return route.fulfill({json:{user:authenticated?{id:'synthetic-jury',username:'jury'}:null}})
  if(path.endsWith('/config'))return route.fulfill({json:{ready:true,max_upload_bytes:1000000,max_duration_sec:180}})
  if(path.endsWith('/analyze'))analysisCalls++
  if(path.includes('/jobs/'))jobCalls++
  return route.fulfill({json:{items:[],total:0}})
 })
 await page.goto(origin+'/documents?job=synthetic-unused')
 await expect(page.getByRole('heading',{name:'Вход в сервис'})).toBeVisible()
 await page.getByLabel('Логин',{exact:true}).fill('synthetic-jury')
 await page.getByLabel('Пароль',{exact:true}).fill('synthetic-password-42')
 await page.getByRole('button',{name:'Войти',exact:true}).click()
 await expect(page.getByRole('heading',{name:'Правовые документы',exact:true})).toBeVisible()
 await expect(page.getByRole('link',{name:'Правовые документы',exact:true})).toHaveAttribute('aria-current','page')
 await expect(page.locator('.legal-document')).toHaveCount(catalog.length)
 expect(await page.locator('.legal-stats dd').allTextContents()).toEqual([String(catalog.length),String(catalog.filter(d=>d.category==='acts').length),String(catalog.filter(d=>d.usedByAnalyzer).length)])
 expect(snapshotRequests).toBe(0)
 await page.getByLabel('Найти документ',{exact:true}).fill('платежность')
 await expect(page.locator('.legal-document')).toHaveCount(1)
 await expect(page.getByRole('heading',{name:'Платёжность банкнот и монет'})).toBeVisible()
 await page.getByLabel('Найти документ',{exact:true}).fill('нет-такого-документа')
 await expect(page.getByRole('heading',{name:'Документы не найдены'})).toBeVisible()
 await page.getByRole('button',{name:'Сбросить фильтры'}).click()
 await page.getByLabel('Раздел',{exact:true}).selectOption('amendments')
 await expect(page.locator('.legal-document')).toHaveCount(catalog.filter(d=>d.category==='amendments').length)
 await page.getByRole('button',{name:'Сбросить фильтры'}).click()
 await page.getByLabel('Только используемые анализатором',{exact:true}).check()
 await expect(page.locator('.legal-document')).toHaveCount(catalog.filter(d=>d.usedByAnalyzer).length)
 await page.getByRole('button',{name:'Сбросить фильтры'}).click()
 for(const width of [320,375,768,1024,1440]){
  await page.setViewportSize({width,height:900})
  const clipped=await page.locator('button,a,input,select,h1,h2,p').evaluateAll(items=>items.filter(el=>{
   if(el.classList.contains('skip-link'))return false
   const r=el.getBoundingClientRect();return r.width>0&&r.height>0&&(r.left<-.5||r.right>innerWidth+.5)
  }).map(el=>({tag:el.tagName,text:el.textContent,rect:el.getBoundingClientRect().toJSON()})))
  expect(clipped,`Viewport ${width}px`).toEqual([])
 }
 await fs.mkdir('tests/results',{recursive:true})
 await page.screenshot({path:'tests/results/legal-documents-desktop.png'})
 await page.setViewportSize({width:375,height:900})
 await page.screenshot({path:'tests/results/legal-documents-mobile.png'})
 let axe=await new AxeBuilder({page}).analyze()
 expect(axe.violations.map(v=>v.id)).toEqual([])
 await page.setViewportSize({width:1440,height:1000})
 const first=page.locator('#NB231')
 const read=first.getByRole('button',{name:'Читать текст'})
 await read.click()
 const dialog=page.getByRole('dialog',{name:catalog.find(d=>d.id==='NB231').title,exact:true})
 await expect(dialog.locator('.legal-text')).toBeVisible()
 expect(await dialog.locator('.legal-text').textContent()).not.toMatch(/^Главная\n/)
 await page.keyboard.press('Escape')
 await expect(dialog).toHaveCount(0)
 await expect(read).toBeFocused()
 const before=snapshotRequests
 await read.click();await expect(dialog.locator('.legal-text')).toBeVisible()
 expect(snapshotRequests).toBe(before)
 for(const [width,height] of [[320,568],[768,900],[1440,1000]]){
  await page.setViewportSize({width,height})
  expect(await dialog.evaluate(el=>{
   const r=el.getBoundingClientRect();return r.left>=0&&r.top>=0&&r.right<=innerWidth+.5&&r.bottom<=innerHeight+.5&&el.scrollWidth<=el.clientWidth+1
  })).toBe(true)
  if(width===320)await page.screenshot({path:'tests/results/legal-reader-mobile.png'})
 }
 axe=await new AxeBuilder({page}).include('.legal-reader').analyze()
 expect(axe.violations.map(v=>v.id)).toEqual([])
 await dialog.getByRole('button',{name:'Закрыть',exact:true}).click()
 const historical=page.locator('#LAW2444')
 await expect(historical.getByText(/часть положений сохраняется до 01.01.2027/)).toBeVisible()
 for(const doc of catalog){
  const article=page.locator('#'+doc.id)
  await expect(article.getByRole('link',{name:'Оригинал в «Әділет»'})).toHaveAttribute('href',doc.url)
  const response=await page.request.get(origin+doc.snapshotUrl)
  expect(response.status()).toBe(200)
  expect(response.headers()['content-type']).toContain('text/plain')
  const body=await response.body()
  expect(body.length).toBe(doc.bytes)
  expect(createHash('sha256').update(body).digest('hex')).toBe(doc.sha256)
 }
 const downloadPromise=page.waitForEvent('download')
 await first.getByRole('link',{name:/Скачать TXT/}).click()
 const download=await downloadPromise
 expect(download.suggestedFilename()).toBe('NB231.txt')
 expect(await download.failure()).toBe(null)
 const errorDoc=catalog.find(d=>d.id==='NB230')
 await page.route('**'+errorDoc.snapshotUrl,route=>route.fulfill({status:503,body:'Synthetic unavailable'}))
 await page.locator('#NB230').getByRole('button',{name:'Читать текст'}).click()
 await expect(page.getByRole('alert')).toContainText('Не удалось загрузить сохранённый текст.')
 await page.unroute('**'+errorDoc.snapshotUrl)
 await page.getByRole('button',{name:'Повторить загрузку'}).click()
 await expect(page.locator('.legal-reader .legal-text')).toBeVisible()
 await page.keyboard.press('Escape')
 await page.getByRole('link',{name:'Проверка видео',exact:true}).click()
 await expect(page.getByRole('heading',{name:'Проверка кассовых операций',exact:true})).toBeVisible()
 await page.getByRole('link',{name:'Правовые документы',exact:true}).click()
 await expect(page.locator('.legal-document')).toHaveCount(catalog.length)
 expect(analysisCalls).toBe(0);expect(jobCalls).toBe(0);expect(errors).toEqual([])
 const result={synthetic_account:true,documents:catalog.length,source_hashes_verified:catalog.length,search:true,filters:true,reader:true,cached_reopen:true,download:true,load_error_retry:true,historical_transition_note:true,authenticated_navigation:true,widths:[320,375,768,1024,1440],axe_violations:0,console_errors:0,analysis_calls:0,job_calls:0}
 await fs.writeFile('tests/results/legal-documents.json',JSON.stringify(result,null,2)+'\n')
 console.log(JSON.stringify(result))
} finally {await browser.close()}
