import { useEffect, useState, type FormEvent } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { LockKeyhole, LoaderCircle, ScanLine } from 'lucide-react'
import { Button } from '../components/ui/button'
import { ApiError, request, type User } from '../lib/api'
import App from '../App'

export default function AuthGate() {
  const [user,setUser]=useState<User|null>()
  const [bootError,setBootError]=useState('')
  const [loggingOut,setLoggingOut]=useState(false)
  const [logoutError,setLogoutError]=useState('')
  const cache=useQueryClient()
  async function load(signal?:AbortSignal) {
    setBootError('')
    try {const result=await request<{user:User|null}>('/auth/me',{signal});setUser(result.user)}
    catch(error){if(!(error instanceof DOMException && error.name==='AbortError'))setBootError(error instanceof Error?error.message:'Не удалось проверить сессию.')}
  }
  useEffect(()=>{
    const controller=new AbortController();load(controller.signal)
    const expired=()=>{cache.clear();setUser(null);setLogoutError('')}
    window.addEventListener('session-expired',expired)
    return ()=>{controller.abort();window.removeEventListener('session-expired',expired)}
  },[cache])
  async function logout(){
    setLoggingOut(true);setLogoutError('')
    try {await request('/auth/logout',{method:'POST'});cache.clear();setUser(null)}
    catch(error){if(error instanceof ApiError && error.status===401){cache.clear();setUser(null)}else setLogoutError('Не удалось выйти. Проверьте соединение и повторите попытку.')}
    finally{setLoggingOut(false)}
  }
  if(user) return <App user={user} onLogout={logout} loggingOut={loggingOut} logoutError={logoutError}/>
  if(user===undefined) return <div className="auth-loading" role="status">{bootError?<><p>{bootError}</p><Button onClick={()=>load()}>Повторить</Button></>:<><LoaderCircle className="spin" size={24}/><p>Проверяем сессию…</p></>}</div>
  return <LoginPage onLogin={value=>{cache.clear();setUser(value)}}/>
}

function LoginPage({onLogin}:{onLogin:(user:User)=>void}) {
  const [username,setUsername]=useState('')
  const [password,setPassword]=useState('')
  const [error,setError]=useState('')
  const [pending,setPending]=useState(false)
  async function submit(event:FormEvent){
    event.preventDefault();setPending(true);setError('')
    try {const result=await request<{user:User}>('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})});setPassword('');onLogin(result.user)}
    catch(error){setError(error instanceof Error?error.message:'Не удалось войти.');setPassword('')}
    finally{setPending(false)}
  }
  return <div className="app-shell login-shell">
    <header className="app-header"><a className="wordmark" href="/"><span className="brand-mark" aria-hidden="true"><ScanLine size={23}/></span><span>kassa<span className="wordmark-dot">.</span></span><span className="wordmark-caption">Видеоанализ</span></a></header>
    <main className="login-page"><section className="login-card" aria-labelledby="login-title">
      <LockKeyhole size={24} className="login-symbol" aria-hidden="true"/>
      <h1 id="login-title">Вход в сервис</h1>
      <p className="login-description">Проверяйте видео и возвращайтесь к сохранённым результатам.</p>
      <form onSubmit={submit} className="login-form">
        <label htmlFor="username">Логин</label><input id="username" name="username" autoComplete="username" maxLength={32} required value={username} onChange={e=>setUsername(e.target.value)} disabled={pending}/>
        <label htmlFor="password">Пароль</label><input id="password" name="password" type="password" autoComplete="current-password" maxLength={128} required value={password} onChange={e=>setPassword(e.target.value)} disabled={pending} aria-describedby={error?'login-error':undefined}/>
        {error&&<p id="login-error" className="login-error" role="alert">{error}</p>}
        <Button type="submit" disabled={pending} aria-busy={pending}>{pending?<><LoaderCircle size={16} className="spin"/>Входим…</>:'Войти'}</Button>
      </form>
      <p className="login-note">Данные для входа выдаёт администратор сервиса.</p>
    </section></main>
  </div>
}
