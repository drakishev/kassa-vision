export type EvidenceFrame = { timestamp_sec: number; image_base64: string }
export type EpisodeReview = {
  classification: {code:string|null; name:string; in_catalog:boolean; title?:string}
  observation: string|null
  reason: string|null
  normative: {document_title:string; reference:string; url:string; conditions:string}|null
}
export type Violation = { type: string; timecode_sec: number; description: string; evidence_frames: EvidenceFrame[]; review?: EpisodeReview }
export type Job = ({ job_id: string; status: 'processing' } | { job_id: string; status: 'failed'; error: string } | { job_id: string; status: 'done'; verdict: 'violation' | 'no_violation'; violations: Violation[]; warnings?: string[] }) & {filename?:string|null}
export type Config = { max_upload_bytes: number; max_duration_sec: number; ready: boolean }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); this.name = 'ApiError' }
}
export function validateFile(file: {name:string; size:number}, max: number): string | null {
  if (!file.name.toLowerCase().endsWith('.mov')) return 'Выберите видео в формате .mov.'
  if (!file.size) return 'Файл пустой. Выберите другую запись.'
  if (file.size > max) return `Файл слишком большой. Допустимо до ${Math.round(max / 1024 / 1024)} МБ.`
  return null
}
const messages: Record<number,string> = {
  429: 'Анализатор занят. Дождитесь завершения текущей проверки и повторите загрузку.',
  401: 'Сессия завершилась. Войдите в учётную запись.',
  403: 'Обновите страницу и повторите загрузку.',
  404: 'Задание недоступно для этой учётной записи. Можно начать новую проверку.',
  413: 'Файл превышает допустимый размер. Выберите другую запись.',
  415: 'Поддерживается формат .mov. Выберите другой файл.',
  422: 'Не удалось прочитать видео. Проверьте файл и повторите загрузку.',
  502: 'Сервис анализа временно недоступен. Повторите попытку позже.',
  503: 'Все места в очереди заняты. Повторите загрузку немного позже.',
}
export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response
  try { response = await fetch(`/ui/api${path}`, { credentials: 'same-origin', ...options }) }
  catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError(0, 'Нет связи с сервером. Проверьте соединение и повторите запрос.')
  }
  if (!response.ok) {
    if(response.status===401 && !path.startsWith('/auth/'))window.dispatchEvent(new Event('session-expired'))
    let message=messages[response.status] ?? 'Не удалось выполнить запрос. Повторите попытку.'
    if(path.startsWith('/auth/')){
      try {const body=await response.json();if(typeof body.error?.message==='string')message=body.error.message}catch{/* Keep bounded local fallback. */}
    }
    throw new ApiError(response.status,message)
  }
  return response.json()
}
export function sourceFromDescription(description: string): {body:string; context?:string; url?:string} {
  const marker = 'Нормативная опора:'
  const index = description.indexOf(marker)
  const rawUrls = description.match(/https?:\/\/[^\s<>]+/g) ?? []
  let url: string | undefined
  for (const candidate of rawUrls) {
    try { const parsed = new URL(candidate.replace(/[.,;]+$/, '')); if (parsed.protocol === 'https:' && parsed.hostname === 'adilet.zan.kz') { url = parsed.href; break } } catch { /* Plain text remains plain text. */ }
  }
  const body = index >= 0 ? description.slice(0,index).trim() : description
  const context = index >= 0 ? description.slice(index + marker.length).replace(/https?:\/\/[^\s<>]+/g, '').trim() : undefined
  return { body, context, url }
}
export function timecode(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole/60).toString().padStart(2,'0')}:${(whole%60).toString().padStart(2,'0')}`
}

export const CANCELLED_MESSAGE = 'Analysis cancelled by user'
export function isCancelled(job?: Job): boolean { return job?.status === 'failed' && job.error === CANCELLED_MESSAGE }

export function safeSourceUrl(value?: string): string | undefined {
  if (!value) return undefined
  try { const url=new URL(value); return url.protocol==='https:' && url.hostname==='adilet.zan.kz' ? url.href : undefined }
  catch { return undefined }
}

export type User = {id:string;username:string}
export type HistoryItem = {job_id:string;filename:string|null;created:number;updated:number;status:'processing'|'done'|'failed'|'cancelled';verdict:'violation'|'no_violation'|null;event_count:number|null}
export type HistoryPage = {items:HistoryItem[];total:number;limit:number;offset:number}
