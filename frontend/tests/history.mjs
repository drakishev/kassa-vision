import {chromium,expect} from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import fs from 'node:fs/promises'
const route=process.env.UI_PATH||'/'
const origin='http://127.0.0.1:18765'
const filename=`history-review-${route==='/v2'?'v2':'v1'}-${Date.now()}.mov`
const historyPath=route==='/v2'?'/v2/history':'/history'
const out=new URL('./results/history/',import.meta.url)
await fs.mkdir(out,{recursive:true})
const browser=await chromium.launch({...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{}),headless:true,args:['--no-sandbox']})
const context=await browser.newContext({viewport:{width:1440,height:1000}})
const page=await context.newPage()
let uploads=0
page.on('request',r=>{if(r.url().endsWith('/ui/api/analyze'))uploads++})
const errors=[];page.on('pageerror',e=>errors.push(e.message))
async function login(username='tester',password='synthetic-password-42'){
 await page.getByLabel('Логин',{exact:true}).fill(username)
 await page.getByLabel('Пароль',{exact:true}).fill(password)
 await page.getByRole('button',{name:'Войти',exact:true}).click()
}
async function widths(){for(const width of [320,375,414,768,1440]){
 await page.setViewportSize({width,height:1000})
 const bad=await page.locator('button,a,input,h1,h2,p').evaluateAll(items=>items.filter(e=>{if(e.classList.contains('skip-link'))return false;const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&(r.left<-.5||r.right>innerWidth+.5)}).map(e=>e.tagName))
 if(bad.length)throw Error('Clipped at '+width+':'+bad)
}}
async function axe(name){const result=await new AxeBuilder({page}).analyze();if(result.violations.length)throw Error(name+JSON.stringify(result.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>n.target)}))))}
await page.goto(origin+historyPath)
await expect(page.getByRole('heading',{name:'Вход в сервис'})).toBeVisible()
await widths();await axe('login');await page.screenshot({path:new URL('login.png',out).pathname,fullPage:true})
await login('tester','wrong-password')
await expect(page.getByRole('alert')).toContainText('Неверный логин или пароль')
await login()
await expect(page.getByRole('heading',{name:'История анализов'})).toBeVisible()
await page.getByRole('link',{name:'Проверка видео',exact:true}).click()
await page.getByLabel('Выберите видео .mov').setInputFiles({name:filename,mimeType:'video/quicktime',buffer:Buffer.from('synthetic history fixture')})
await page.getByRole('button',{name:'Начать проверку'}).click()
await expect(page.getByText('Обнаружены признаки возможных нарушений',{exact:true})).toBeVisible({timeout:15000})
const job=new URL(page.url()).searchParams.get('job')
await page.getByRole('link',{name:'История',exact:true}).click()
await expect(page.getByRole('heading',{name:filename,exact:true})).toBeVisible()
await page.goto(origin+'/v2/history?source=saved#archive')
await expect(page.getByRole('heading',{name:'История анализов'})).toBeVisible()
const canonicalUrl=new URL(page.url())
if(canonicalUrl.pathname!=='/history'||canonicalUrl.search!=='?source=saved'||canonicalUrl.hash!=='#archive')throw Error('Legacy history URL lost its state')
await expect(page.getByRole('link',{name:/Minecraft|Версия 1/})).toHaveCount(0)
await expect(page.getByRole('heading',{name:filename,exact:true})).toBeVisible()
await widths();await axe('history');await page.screenshot({path:new URL('history.png',out).pathname,fullPage:true})
const before=uploads
await page.locator('.history-row').filter({has:page.getByRole('heading',{name:filename,exact:true})}).getByRole('link',{name:'Открыть результат'}).click()
await expect(page.getByText('Обнаружены признаки возможных нарушений',{exact:true})).toBeVisible()
if(uploads!==before)throw Error('Reopening launched new analysis')
await page.getByRole('button',{name:'Выйти',exact:true}).click()
await expect(page.getByRole('heading',{name:'Вход в сервис'})).toBeVisible()
if((await context.request.get(origin+'/ui/api/jobs/'+job)).status()!==401)throw Error('Logout did not revoke access')
await login('other')
await expect(page.getByRole('heading',{name:'Не удалось получить результат'})).toBeVisible()
await expect(page.getByText('Обнаружены признаки возможных нарушений',{exact:true})).not.toBeVisible()
await page.getByRole('link',{name:'История',exact:true}).click()
await expect(page.getByRole('heading',{name:filename,exact:true})).not.toBeVisible()
await page.getByRole('button',{name:'Выйти',exact:true}).click();await login('tester')
await expect(page.getByRole('heading',{name:filename,exact:true})).toBeVisible()
const newContext=await browser.newContext();const fresh=await newContext.newPage()
await fresh.goto(origin+historyPath)
await fresh.getByLabel('Логин',{exact:true}).fill('tester');await fresh.getByLabel('Пароль',{exact:true}).fill('synthetic-password-42');await fresh.getByRole('button',{name:'Войти',exact:true}).click()
await expect(fresh.getByRole('heading',{name:filename,exact:true})).toBeVisible()
if(errors.length)throw Error(JSON.stringify(errors))
const report={fixture_only:true,legacy_history_preserved:true,login_validated:true,invalid_password_rejected:true,logout_revoked:true,other_account_isolated:true,same_account_new_browser_restored:true,reopen_posts:uploads-before,axe_violations:0,widths:[320,375,414,768,1440]}
await fs.writeFile(new URL('report.json',out),JSON.stringify(report,null,2));console.log(JSON.stringify(report));await browser.close()
