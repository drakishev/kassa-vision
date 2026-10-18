import {useState} from 'react'
import {useQuery} from '@tanstack/react-query'
import {ArrowUpRight,BookOpen,Download,Search} from 'lucide-react'
import {Button} from './components/ui/button'
import {Dialog,DialogContent,DialogDescription,DialogHeader,DialogTitle,DialogTrigger} from './components/ui/dialog'
import catalog from '../public/assets/legal-documents/catalog.json'
import './legal-documents.css'

type LegalDocument = (typeof catalog.documents)[number]
const documents=catalog.documents
const citedDocuments=documents.filter(doc=>doc.usedByAnalyzer)
const categories:Record<string,string>={acts:'Основные акты',amendments:'Изменения',historical:'Исторические источники'}
const dateFormat=new Intl.DateTimeFormat('ru-RU',{day:'numeric',month:'long',year:'numeric',timeZone:'UTC'})
const normalize=(value:string)=>value.toLocaleLowerCase('ru-RU').replace(/ё/g,'е')
const fileSize=(bytes:number)=>bytes>=1024*1024?`${(bytes/(1024*1024)).toFixed(1)} МБ`:`${Math.ceil(bytes/1024)} КБ`

export default function LegalDocuments() {
  const [search,setSearch]=useState('')
  const [category,setCategory]=useState('all')
  const [usedOnly,setUsedOnly]=useState(false)
  const query=normalize(search.trim())
  const shown=documents.filter(doc=>(category==='all'||doc.category===category)&&(!usedOnly||doc.usedByAnalyzer)
    &&normalize([doc.title,doc.officialTitle,doc.reference,doc.scope,doc.id,doc.code].join(' ')).includes(query))
  function reset(){setSearch('');setCategory('all');setUsedOnly(false)}
  return <section className="legal-page" aria-labelledby="legal-title">
    <div className="page-heading"><div><h1 id="legal-title">Правовые документы</h1><p>Источники, собранные для проекта. Сохранённые тексты и ссылки на оригиналы в «Әділет».</p></div><a className="history-open" href="/assets/legal-documents/catalog.json" download="legal-documents.json">Скачать реестр<Download size={16} aria-hidden="true"/></a></div>
    <dl className="legal-stats" aria-label="Состав коллекции">
      <div><dt>Документов в коллекции</dt><dd>{documents.length}</dd></div>
      <div><dt>Основных актов</dt><dd>{documents.filter(doc=>doc.category==='acts').length}</dd></div>
      <div><dt>Источников в нормативных ссылках</dt><dd>{citedDocuments.length}</dd></div>
    </dl>
    <p className="legal-context legal-usage">В нормативных ссылках к выводам используются {citedDocuments.map((doc,index)=><span key={doc.id}>{index>0?' и ':''}<a href={`#${doc.id}`}>{doc.reference.split(' от ')[0]}</a></span>)}. Остальные документы собраны как справочные материалы. Полнотекстовый поиск анализатором по всей коллекции пока не выполняется.</p>
    <p className="legal-context">Основные акты, документы с изменениями и исторические источники учитываются отдельно. Дата рядом с документом — дата сохранения текста; актуальную редакцию можно открыть в «Әділет».</p>
    <div className="legal-filters" role="search" aria-label="Поиск по правовым документам">
      <label className="legal-search">Найти документ<span><Search size={18} aria-hidden="true"/><input type="search" value={search} onChange={e=>setSearch(e.target.value)} placeholder="Название, номер или тема"/></span></label>
      <div className="legal-category"><label htmlFor="legal-category">Раздел</label><select id="legal-category" value={category} onChange={e=>setCategory(e.target.value)}><option value="all">Все документы · {documents.length}</option>{Object.entries(categories).map(([key,label])=><option key={key} value={key}>{label} · {documents.filter(doc=>doc.category===key).length}</option>)}</select></div>
      <label className="legal-used-only"><input type="checkbox" checked={usedOnly} onChange={e=>setUsedOnly(e.target.checked)}/>Только используемые анализатором</label>
    </div>
    <div className="legal-results-heading"><p role="status">Показано {shown.length} из {documents.length}</p>{(search||category!=='all'||usedOnly)&&<Button variant="ghost" onClick={reset}>Сбросить фильтры</Button>}</div>
    {shown.length?<ul className="legal-list">{shown.map(doc=><li key={doc.id} id={doc.id}>
      <article className="legal-document" aria-labelledby={`title-${doc.id}`}>
        <div className="legal-document-content">
          <div className="legal-document-labels"><span>{categories[doc.category]}</span><span className="legal-id">{doc.id}</span>{doc.usedByAnalyzer&&<span className="legal-active">В текущем анализаторе</span>}</div>
          <h2 id={`title-${doc.id}`}>{doc.title}</h2>
          <p className="legal-reference">{doc.reference}</p>
          <p className="legal-scope">{doc.scope}</p>
          <p className="legal-document-note">{doc.note}</p>
          <details className="legal-details"><summary>Реквизиты и сохранённая копия</summary><p>{doc.officialTitle}</p><dl><div><dt>Сохранено</dt><dd><time dateTime={doc.capturedAt}>{dateFormat.format(new Date(doc.capturedAt))}</time></dd></div><div><dt>Код в «Әділет»</dt><dd>{doc.code}</dd></div><div><dt>Размер текста</dt><dd>{fileSize(doc.bytes)}</dd></div><div><dt>SHA-256 копии</dt><dd className="legal-checksum">{doc.sha256}</dd></div></dl></details>
        </div>
        <div className="legal-document-actions">
          <DocumentPreview doc={doc}/>
          <a className="legal-link" href={doc.url} target="_blank" rel="noopener noreferrer">Оригинал в «Әділет»<ArrowUpRight size={16} aria-hidden="true"/></a>
          <a className="legal-link legal-download" href={doc.snapshotUrl} download={`${doc.id}.txt`}><Download size={16} aria-hidden="true"/>Скачать TXT · {fileSize(doc.bytes)}</a>
          <span className="legal-saved">Сохранено {dateFormat.format(new Date(doc.capturedAt))}</span>
        </div>
      </article>
    </li>)}</ul>:<div className="legal-empty"><BookOpen size={28} aria-hidden="true"/><h2>Документы не найдены</h2><p>Измените запрос или сбросьте фильтры.</p></div>}
  </section>
}

function DocumentPreview({doc}:{doc:LegalDocument}) {
  return <Dialog><DialogTrigger asChild><Button variant="outline" className="legal-read"><BookOpen size={16} aria-hidden="true"/>Читать текст</Button></DialogTrigger>
    <DialogContent className="legal-reader">
      <DialogHeader className="legal-reader-heading"><DialogTitle>{doc.title}</DialogTitle><DialogDescription>{doc.reference} · копия от {dateFormat.format(new Date(doc.capturedAt))}</DialogDescription></DialogHeader>
      <DocumentText doc={doc}/>
    </DialogContent>
  </Dialog>
}

function DocumentText({doc}:{doc:LegalDocument}) {
  const query=useQuery({queryKey:['legal-document',doc.code,doc.sha256],staleTime:Infinity,queryFn:async({signal})=>{
    const response=await fetch(doc.snapshotUrl,{signal})
    if(!response.ok||!response.headers.get('content-type')?.startsWith('text/plain'))throw new Error('Не удалось загрузить сохранённый текст.')
    const raw=await response.text()
    // Omit the captured site's navigation in the reader; the download is unchanged.
    const marker='\nСравнить языки\n\n',start=raw.indexOf(marker)
    return start>=0?raw.slice(start+marker.length):raw
  }})
  return <div className="legal-reader-body">
    <div className="legal-reader-links"><a href={doc.url} target="_blank" rel="noopener noreferrer">Оригинал в «Әділет»<ArrowUpRight size={15} aria-hidden="true"/></a><a href={doc.snapshotUrl} download={`${doc.id}.txt`}>Скачать TXT<Download size={15} aria-hidden="true"/></a></div>
    {query.isPending&&<p role="status">Загружаем текст документа…</p>}
    {query.isError&&<div role="alert"><p>{query.error.message}</p><Button variant="outline" onClick={()=>query.refetch()}>Повторить загрузку</Button></div>}
    {query.data&&<pre className="legal-text" tabIndex={0} aria-label="Сохранённый текст документа">{query.data}</pre>}
  </div>
}
