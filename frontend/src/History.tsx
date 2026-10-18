import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Clock3, FileVideo, LoaderCircle } from 'lucide-react'
import { Button } from './components/ui/button'
import { request, type HistoryPage, type User } from './lib/api'

const dateFormat=new Intl.DateTimeFormat('ru-RU',{dateStyle:'medium',timeStyle:'short'})
export default function History({user}:{user:User}) {
  const [page,setPage]=useState(0)
  const history=useQuery({queryKey:['history',user.id,page],queryFn:({signal})=>request<HistoryPage>(`/history?limit=20&offset=${page*20}`,{signal}),
    refetchInterval:q=>q.state.data?.items.some(item=>item.status==='processing')?5_000:false})
  const base='/'
  return <section className="history-page" aria-labelledby="history-title">
    <div className="page-heading"><div><h1 id="history-title">История анализов</h1><p>Открывайте готовые результаты без повторного анализа.</p></div></div>
    {history.isPending&&<div className="history-message" role="status"><LoaderCircle size={24} className="spin"/>Загружаем историю…</div>}
    {history.isError&&<div className="history-message" role="alert"><p>{history.error.message}</p><Button variant="outline" onClick={()=>history.refetch()}>Повторить</Button></div>}
    {history.data&&<>
      <p className="history-count">Сохранено проверок: {history.data.total}</p>
      {!history.data.items.length?<div className="history-message"><FileVideo size={32}/><h2>Здесь появятся ваши проверки</h2><p>Загрузите первую запись — результат сохранится автоматически.</p><a className="history-open" href={base}>Проверить видео<ArrowRight size={16}/></a></div>:<ul className="history-list">{history.data.items.map(item=>{
        const status=item.status==='done'?(item.verdict==='violation'?'Возможные нарушения':'Нарушение не обнаружено'):item.status==='cancelled'?'Анализ отменён':item.status==='failed'?'Ошибка обработки':'Выполняется'
        const tone=item.status==='done'?(item.verdict==='violation'?'attention':'success'):item.status==='failed'?'error':'neutral'
        return <li key={item.job_id} className="history-row">
          <div className="history-file"><FileVideo size={20} aria-hidden="true"/><div><h2 title={item.filename??undefined}>{item.filename||`Проверка ${item.job_id.slice(0,8)}`}</h2><p><Clock3 size={13}/><time dateTime={new Date(item.created*1000).toISOString()}>{dateFormat.format(item.created*1000)}</time><span className="history-id">{item.job_id.slice(0,8)}</span></p></div></div>
          <div className="history-outcome"><span className={`history-status tone-${tone}`}>{status}</span>{item.event_count!==null&&<span className="history-events">Эпизодов: {item.event_count}</span>}</div>
          <a className="history-open" href={`${base}?job=${encodeURIComponent(item.job_id)}`}>{item.status==='processing'?'Открыть проверку':'Открыть результат'}<ArrowRight size={16}/></a>
        </li>
      })}</ul>}
      {history.data.total>20&&<nav className="history-pagination" aria-label="Страницы истории"><Button variant="outline" disabled={page===0} onClick={()=>setPage(page-1)}><ArrowLeft size={16}/>Назад</Button><span>{page+1} / {Math.ceil(history.data.total/20)}</span><Button variant="outline" disabled={(page+1)*20>=history.data.total} onClick={()=>setPage(page+1)}>Далее<ArrowRight size={16}/></Button></nav>}
    </>}
  </section>
}
