import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, ArrowUpRight, Check, CheckCheck, ChevronRight, CircleAlert, Clock3, FileVideo, Info, LoaderCircle, Maximize2, Play, ScanLine, ShieldCheck, Upload, X } from 'lucide-react'
import History from './History'
import LegalDocuments from './LegalDocuments'
import ModelDiagnostics from './ModelDiagnostics'
import { Button } from './components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from './components/ui/dialog'
import { ApiError, isCancelled, request, safeSourceUrl, sourceFromDescription, timecode, validateFile, type Config, type Job, type Violation, type User } from './lib/api'

const getJob = () => new URLSearchParams(location.search).get('job') || ''

export default function App({user,onLogout,loggingOut,logoutError}: {user:User;onLogout:()=>Promise<void>;loggingOut:boolean;logoutError:string}) {
  const historyView=location.pathname.endsWith('/history')
  const documentsView=location.pathname.replace(/\/$/,'')==='/documents'
  const base='/'
  const [jobId, setJobId] = useState(getJob)
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState('')
  const [drag, setDrag] = useState(false)
  const [started, setStarted] = useState<number | null>(null)
  const [elapsed, setElapsed] = useState(0)
  const [eventIndex, setEventIndex] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const cancelAfterUpload = useRef(false)
  const [cancelQueued,setCancelQueued] = useState(false)
  const queryClient = useQueryClient()
  const config = useQuery({ queryKey: ['config',user.id], queryFn: () => request<Config>('/config'), refetchInterval: 30_000 })
  const job = useQuery({
    queryKey: ['job',user.id,jobId], enabled: Boolean(jobId) && !historyView && !documentsView,
    queryFn: ({ signal }) => request<Job>(`/jobs/${encodeURIComponent(jobId)}`, { signal }),
    refetchInterval: q => q.state.error instanceof ApiError && q.state.error.status === 404 ? false : q.state.data?.status === 'processing' ? 2_000 : false,
  })
  function updateJob(id: string) {
    const url = new URL(location.href)
    if (id) url.searchParams.set('job', id); else url.searchParams.delete('job')
    history.replaceState({}, '', url); setJobId(id)
  }
  const cancel = useMutation({
    mutationFn: (id:string) => request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, {method:'POST'}),
    onSuccess: async result => {
      await queryClient.cancelQueries({queryKey:['job',user.id,result.job_id]})
      queryClient.setQueryData(['job',user.id,result.job_id],result)
      if (isCancelled(result)) {setFile(null);setStarted(null);if(input.current)input.current.value=''}
    },
    onSettled: () => {cancelAfterUpload.current=false;setCancelQueued(false)},
  })
  const upload = useMutation({
    mutationFn: async (record: File) => { const data = new FormData(); data.append('file', record); return request<Job>('/analyze', {method:'POST', body:data}) },
    onSuccess: result => { updateJob(result.job_id); setEventIndex(0);if(cancelAfterUpload.current)cancel.mutate(result.job_id) },
    onError: () => {cancelAfterUpload.current=false;setCancelQueued(false)},
  })
  const result = job.data?.status === 'done' ? job.data : null
  const inaccessibleJob = job.error instanceof ApiError && job.error.status === 404
  const pendingJob = Boolean(jobId) && !inaccessibleJob && (!job.data || job.data.status === 'processing')
  const processing = pendingJob && !job.isError
  const busy = upload.isPending || pendingJob || cancel.isPending
  const cancelled = isCancelled(job.data)
  const failed = job.data?.status === 'failed' && !cancelled
  const max = config.data?.max_upload_bytes ?? 256 * 1024 * 1024

  useEffect(() => {
    if (!busy || !started) return
    setElapsed(Math.floor((Date.now() - started) / 1000))
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => clearInterval(id)
  }, [busy, started])

  function select(record?: File) {
    if (!record || busy) return
    const error = validateFile(record, max)
    setFileError(error ?? ''); setFile(error ? null : record); upload.reset();cancel.reset()
    if (!error) { updateJob(''); setStarted(null); setElapsed(0) }
  }
  function begin() {
    if (!file) { input.current?.click(); return }
    const error = validateFile(file, max)
    if (error) {setFileError(error);return}
    setStarted(Date.now()); setElapsed(0); upload.mutate(file)
  }
  function cancelAnalysis() {
    if (upload.isPending) {cancelAfterUpload.current=true;setCancelQueued(true);return}
    if (jobId) cancel.mutate(jobId)
  }
  function reset() {
    updateJob(''); setFile(null); setFileError(''); setStarted(null); setElapsed(0); upload.reset();cancel.reset();cancelAfterUpload.current=false;setCancelQueued(false);setEventIndex(0)
    if (input.current) input.current.value = ''
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main">К содержимому страницы</a>
    <header className="app-header">
      <a className="wordmark" href={base}><span className="brand-mark" aria-hidden="true"><ScanLine size={23}/></span><span>kassa<span className="wordmark-dot">.</span></span><span className="wordmark-caption">Видеоанализ</span></a>
      <div className="header-actions">
        <span className={`service-state ${config.data?.ready ? 'is-ready' : ''}`} role="status"><span className="status-dot"/>{config.data?.ready ? 'Готов к анализу' : config.isError ? 'Нет связи' : 'Анализатор не готов'}</span>
        <Dialog><DialogTrigger asChild><Button variant="ghost" className="help-button" aria-label="О проверке"><Info size={16}/><span>О проверке</span></Button></DialogTrigger><DialogContent className="info-dialog"><DialogHeader><DialogTitle>Проверка кассовых операций</DialogTitle><DialogDescription>Загрузите запись, дождитесь результата и проверьте отмеченные эпизоды.</DialogDescription></DialogHeader><div className="info-body"><p>Для каждого события сервис показывает время, исходные кадры и обоснование со ссылкой на нормативный источник.</p><p>Результат — подсказка для проверяющего. Учитывайте видимость деталей и ограничения анализа.</p><p>Поддерживается .mov. Записи и модели обрабатываются на локальном сервере без облачных AI-сервисов.</p></div></DialogContent></Dialog>
      </div>
      <div className="account-bar"><nav className="account-nav" aria-label="Рабочее место"><a href={base} aria-current={!historyView&&!documentsView?'page':undefined}>Проверка видео</a><a href="/history" aria-current={historyView?'page':undefined}>История</a><a href="/annotate/">Разметка</a><a href="/documents" aria-current={documentsView?'page':undefined}>Правовые документы</a></nav><div className="account-user"><span title={user.username}>{user.username}</span><Button variant="ghost" onClick={onLogout} disabled={loggingOut || upload.isPending || cancel.isPending}>{loggingOut?'Выходим…':'Выйти'}</Button></div></div>
      {logoutError&&<p className="logout-error" role="alert">{logoutError}</p>}
    </header>

    <main id="main" className="main-content">
      {documentsView?<LegalDocuments/>:historyView?<History user={user}/>:<>
      <div className="page-heading"><div><h1>Проверка кассовых операций</h1><p>От записи — к конкретному эпизоду и его основанию.</p></div><span className="workspace-label">Рабочее место проверки</span></div>
      <ol className="workflow" aria-label="Этапы проверки">
        {[['Запись',Boolean(file || jobId)],['Анализ',Boolean(result || failed || cancelled)],['Результат',Boolean(result)]] .map(([label,done],i) => <li key={String(label)} className={done ? 'complete' : i === (busy ? 1 : result || failed || cancelled ? 2 : 0) ? 'current' : ''}><span className="step-marker">{done ? <Check size={12}/> : i+1}</span><span>{String(label)}</span>{i<2 && <span className="step-rule"/>}</li>)}
      </ol>

      <div className="workbench">
        <aside className="upload-panel" aria-labelledby="upload-title">
          <div className="panel-heading"><h2 id="upload-title">Запись для анализа</h2><FileVideo size={18} aria-hidden="true"/></div>
          {job.data?.filename&&!file&&<p className="restored-filename" title={job.data.filename}>{job.data.filename}</p>}
          <input ref={input} type="file" id="video-file" className="sr-only" accept=".mov,video/quicktime" aria-label="Выберите видео .mov" aria-describedby={fileError ? 'file-error' : 'file-hint'} aria-invalid={Boolean(fileError)} disabled={busy} onChange={e => select(e.target.files?.[0])}/>
          <button type="button" className={`drop-zone ${drag ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`} disabled={busy} onClick={() => input.current?.click()} onDragOver={e => { e.preventDefault(); if (!busy) setDrag(true) }} onDragLeave={() => setDrag(false)} onDrop={e => { e.preventDefault(); setDrag(false); if (e.dataTransfer.files.length > 1) setFileError('Выберите одну запись для проверки.'); else select(e.dataTransfer.files[0]) }}>
            <span className="upload-glyph" aria-hidden="true">{file ? <FileVideo size={26} strokeWidth={1.4}/> : <Upload size={26} strokeWidth={1.4}/>}</span>
            <span className="drop-label">{file ? 'Изменить файл' : 'Выбрать видео'}</span>
          </button>
          <p id="file-hint" className="file-hint">Перетащите запись в область выше<br/>.mov · до {Math.round(max/1024/1024)} МБ</p>
          {file && <div className="selected-file"><FileVideo size={18}/><div><span className="filename" title={file.name}>{file.name}</span><span className="file-size">{(file.size/1024/1024).toFixed(1)} МБ</span></div>{!busy && <button className="icon-button" onClick={reset} aria-label="Убрать файл"><X size={15}/></button>}</div>}
          {fileError && <p className="inline-error" id="file-error" role="alert"><CircleAlert size={16}/>{fileError}</p>}
          {upload.isError && <div className="request-error" role="alert"><CircleAlert size={17}/><p>{upload.error.message}</p></div>}
          <Button className="analyze-button" onClick={begin} disabled={busy || !config.data?.ready || Boolean(fileError)} aria-busy={busy}>
            {busy ? <><LoaderCircle size={17} className="spin"/>{upload.isPending ? 'Загружаем запись' : 'Идёт анализ'}</> : <>{file ? 'Начать проверку' : 'Выбрать запись'}<ArrowRight size={17}/></>}
          </Button>
          {busy && <Button variant="outline" className="cancel-analysis" onClick={cancelAnalysis} disabled={cancel.isPending || cancelQueued}>{cancel.isPending || cancelQueued ? <><LoaderCircle size={16} className="spin"/>Останавливаем…</> : <><X size={16}/>Отменить анализ</>}</Button>}
          {cancel.isError && <p className="inline-error" role="alert">Отмена не подтверждена. {cancel.error.message}</p>}
          {cancelQueued && upload.isPending && <p className="availability-note" role="status">Файл ещё передаётся. После приёма сервис сразу отменит задание.</p>}
          {cancel.data?.status === 'done' && <p className="availability-note" role="status">Анализ успел завершиться до отмены. Результат сохранён.</p>}
          {!config.data?.ready && <p className="availability-note">{config.isError ? 'Проверьте соединение с сервером.' : 'Анализатор пока не готов. Статус обновится автоматически.'}</p>}
          {!busy && (result || failed || cancelled || job.isError) && <Button variant="ghost" className="new-check" onClick={reset}>Новая проверка<ArrowRight size={15}/></Button>}
          <div className="local-note"><ShieldCheck size={17}/><p>Обработка внутри сервиса.<br/><span>Без передачи в облачные AI.</span></p></div>
        </aside>

        <section className="review-panel" aria-label="Результат проверки" aria-live="polite" aria-busy={busy}>
          {!jobId && !upload.isPending && <div className="empty-state"><div className="empty-illustration" aria-hidden="true"><div className="scan-corner top-left"/><div className="scan-corner top-right"/><div className="scan-corner bottom-left"/><div className="scan-corner bottom-right"/><FileVideo size={43} strokeWidth={1}/><span className="scan-rule"/></div><h2>Каждому выводу — доказательство</h2><p>Загрузите видео, чтобы найти эпизоды<br className="desktop-break"/> и проверить их по исходным кадрам.</p><div className="output-legend"><span>Таймкод</span><span>Кадры</span><span>Основание</span></div></div>}
          {(upload.isPending || processing) && <div className="processing-state"><div className="processing-symbol"><ScanLine size={36} strokeWidth={1.25}/><span/></div><span className="processing-label">{upload.isPending ? 'Передача записи' : 'Проверка выполняется'}</span><h2>{upload.isPending ? 'Загружаем видео' : 'Изучаем запись'}</h2><p>Результат появится здесь после завершения анализа.<br/>Можно оставить страницу открытой.</p>{started && <div className="elapsed" aria-live="off"><Clock3 size={16}/><span>{timecode(elapsed)}</span><span>прошло</span></div>}<div className="indeterminate" role="progressbar" aria-label="Анализ видео выполняется"><span/></div>{elapsed>300 && <p className="slow-note">Обработка длится больше пяти минут. Статус продолжает обновляться.</p>}</div>}
          {job.isError && <div className="failure-state"><CircleAlert size={32}/><h2>Не удалось получить результат</h2><p>{job.error.message}</p><Button variant="outline" onClick={() => job.refetch()}>Обновить статус</Button></div>}
          {cancelled && <div className="cancelled-state"><X size={32}/><h2>Анализ отменён</h2><p>Загрузите другую запись, чтобы начать новую проверку.</p></div>}
          {failed && <div className="failure-state"><CircleAlert size={32}/><h2>Обработка не завершена</h2><p>Анализатор не смог обработать запись.<br/>Начните новую проверку или повторите её позже.</p><Button variant="outline" onClick={reset}>Новая проверка</Button></div>}
          {result?.verdict === 'no_violation' && <div className="negative-state"><div className="negative-symbol"><CheckCheck size={31} strokeWidth={1.4}/></div><span className="result-label">Проверка завершена</span><h2>Нарушение не обнаружено</h2><p>На доступных кадрах сервис не выделил<br className="desktop-break"/> признаков возможного нарушения.</p><p className="result-caveat">Результат относится к видимой части записи.</p><Warnings warnings={result.warnings}/></div>}
          {result?.verdict === 'violation' && <>
            <div className="result-heading"><div className="verdict-line"><span className="attention-dot"/><span>Обнаружены признаки возможных нарушений</span></div><p>Проверьте выделенные эпизоды и обоснования.</p></div>
            <div className="event-navigation" aria-label="Найденные эпизоды">{result.violations.map((event,index) => <button key={index} className={eventIndex===index?'selected':''} aria-pressed={eventIndex===index} onClick={() => setEventIndex(index)}><span>Эпизод {index+1}</span><span className="event-time">{timecode(event.timecode_sec)}</span></button>)}</div>
            <EventReview key={`${jobId}-${eventIndex}`} event={result.violations[Math.min(eventIndex,result.violations.length-1)]} index={eventIndex} jobId={jobId}/>
            <Warnings warnings={result.warnings}/>
          </>}
        </section>
      </div>
      {result&&<ModelDiagnostics key={jobId} jobId={jobId}/>}
      </>}
      <footer className="app-footer"><span>Kassa Vision</span><span>Решение помогает проверяющему оценить наблюдаемые действия.</span></footer>
    </main>
  </div>
}

function Warnings({warnings}: {warnings?:string[]}) {
  if (!warnings?.length) return null
  return <details className="warnings"><summary><Info size={15}/>Ограничения проверки<ChevronRight size={15}/></summary><ul>{warnings.map((text,i) => <li key={i}>{text}</li>)}</ul></details>
}

function EventReview({event,index,jobId}: {event:Violation; index:number; jobId:string}) {
  const [frameIndex,setFrameIndex]=useState(0)
  const [preview,setPreview]=useState(false)
  const [videoError,setVideoError]=useState(false)
  const video=useRef<HTMLVideoElement>(null)
  const frame=event.evidence_frames[frameIndex]
  const source=sourceFromDescription(event.description)
  const review=event.review
  const normative=review?.normative
  const sourceUrl=safeSourceUrl(normative?.url ?? source.url)
  return <div className="event-content">
    <div className="evidence-column">
      <Dialog><DialogTrigger asChild><button className="evidence-main" aria-label={`Увеличить кадр ${timecode(frame.timestamp_sec)}`}><img src={`data:image/jpeg;base64,${frame.image_base64}`} alt={`Кадр-доказательство эпизода ${index+1}, ${timecode(frame.timestamp_sec)}`}/><span className="frame-meta"><span>{timecode(frame.timestamp_sec)}</span><Maximize2 size={15}/></span></button></DialogTrigger><DialogContent className="frame-dialog"><DialogHeader><DialogTitle>Кадр-доказательство · {timecode(frame.timestamp_sec)}</DialogTitle><DialogDescription>Исходный кадр из загруженного видео.</DialogDescription></DialogHeader><img src={`data:image/jpeg;base64,${frame.image_base64}`} alt={`Увеличенный кадр ${timecode(frame.timestamp_sec)}`}/></DialogContent></Dialog>
      {event.evidence_frames.length>1 && <div className="frame-thumbnails" aria-label="Кадры эпизода">{event.evidence_frames.map((f,i) => <button key={i} onClick={() => setFrameIndex(i)} aria-pressed={i===frameIndex} aria-label={`Показать кадр ${timecode(f.timestamp_sec)}`}><img src={`data:image/jpeg;base64,${f.image_base64}`} alt=""/><span>{timecode(f.timestamp_sec)}</span></button>)}</div>}
      <p className="evidence-caption">Исходные кадры · без изменения содержимого</p>
    </div>
    <div className="event-explanation">
      <div className="event-kicker"><span>Эпизод {index+1}</span><span className="timestamp"><Clock3 size={14}/>{timecode(event.timecode_sec)}</span></div>
      <div className="classification"><span className="classification-label">Класс нарушения</span><span className="class-code">{review ? review.classification.code ?? 'Вне каталога' : 'Код не сохранён'}</span></div>
      <h2>{review?.classification.title ?? review?.classification.name ?? event.type}</h2>
      {review?.classification.title && review.classification.title !== review.classification.name && <details className="class-definition"><summary>Критерий класса</summary><p>{review.classification.name}</p></details>}
      <div className="observation"><h3>{review?.observation ? 'Что видно на записи' : 'Описание эпизода'}</h3><p>{review?.observation ?? source.body}</p></div>
      <div className="episode-reason"><h3>Почему это может быть нарушением</h3><p>{review?.reason ?? 'Отдельное объяснение причины не сохранено. Проверьте описание эпизода и исходные кадры.'}</p></div>
      <div className="rule-reference"><span className="rule-icon"><ShieldCheck size={18}/></span><div>
        <h3>Нормативное основание</h3>
        {normative ? <><p className="normative-document">{normative.document_title}</p><p className="normative-reference">{normative.reference}</p><p>{normative.conditions}</p></> : <p>{source.context ?? 'Конкретное нормативное основание в ответе не установлено. Требуется дополнительная проверка.'}</p>}
        {sourceUrl && <a href={sourceUrl} target="_blank" rel="noopener noreferrer">Открыть источник<ArrowUpRight size={14}/></a>}
      </div></div>
      <div className="video-preview"><button className="video-toggle" onClick={() => {setPreview(!preview);setVideoError(false)}} aria-expanded={preview}><Play size={15}/>{preview ? 'Скрыть видео' : 'Посмотреть видео'}<ChevronRight size={15}/></button>{preview && <div className="video-player">{videoError ? <p>Предпросмотр этой записи недоступен. Проверьте кадры-доказательства выше.</p> : <video ref={video} controls preload="metadata" src={`/ui/api/jobs/${jobId}/media`} onLoadedMetadata={() => {if(video.current)video.current.currentTime=event.timecode_sec}} onError={() => setVideoError(true)} aria-label="Исходная видеозапись"/>}</div>}</div>
    </div>
  </div>
}
