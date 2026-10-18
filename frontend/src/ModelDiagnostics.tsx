import {useEffect,useRef,useState,type CSSProperties} from 'react'
import {useQuery} from '@tanstack/react-query'
import {Maximize2} from 'lucide-react'
import {Dialog,DialogContent,DialogDescription,DialogHeader,DialogTitle,DialogTrigger} from './components/ui/dialog'
import {ApiError,request,timecode} from './lib/api'
import {BODY_EDGES,anchorFrame,layerAvailable,savedLayers,visibleDetections,visiblePoses,visiblePoint,type Diagnostics,type DiagnosticFrame,type Layer} from './lib/diagnostics'
import './model-diagnostics.css'

const layers:[Layer,string][]=[['original','Исходный кадр'],['detection','Детекция'],['pose','Поза'],['segmentation','Сегментация'],['states','Состояния']]
const modelNames:Record<string,Partial<Record<Layer,string>>>={
  'fusion-v1':{original:'Сохранённый кадр',detection:'Grounding DINO · ByteTrack',pose:'ViTPose',segmentation:'SAM 2.1 · опорный кадр',states:'SigLIP2 · предварительная оценка'},
  'legacy-supported':{original:'Сохранённый кадр',detection:'Grounding DINO · ByteTrack',pose:'RTMW · 17 точек тела'},
}

/** Opens only on request. Saved model output is read without running inference. */
export default function ModelDiagnostics({jobId}:{jobId:string}) {
  const [open,setOpen]=useState(false)
  const query=useQuery({queryKey:['model-diagnostics',jobId],queryFn:({signal})=>request<Diagnostics>(`/jobs/${encodeURIComponent(jobId)}/diagnostics`,{signal}),enabled:open,staleTime:Infinity})
  return <section className="model-diagnostics" aria-label="Диагностика моделей">
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><button type="button" className="diagnostic-toggle"><Maximize2 size={16} aria-hidden="true"/>Показать работу моделей</button></DialogTrigger>
      <DialogContent className="model-diagnostics-dialog">
        <DialogHeader className="diagnostic-heading">
          <DialogTitle>Диагностика моделей</DialogTitle>
          <DialogDescription>Сохранённые кадры с наложениями моделей. Повторный анализ не запускается.</DialogDescription>
        </DialogHeader>
        <div className="diagnostic-content">
          {query.isPending&&<p role="status">Загружаем измерения…</p>}
          {query.isError&&<div role="status"><p>{query.error instanceof ApiError&&query.error.status===404?'Для этой проверки измерения моделей не сохранены.':query.error.message}</p><button type="button" onClick={()=>query.refetch()}>Повторить загрузку</button></div>}
          {query.data&&<DiagnosticViewer key={jobId} data={query.data} jobId={jobId}/>}
        </div>
      </DialogContent>
    </Dialog>
  </section>
}

function DiagnosticViewer({data,jobId}:{data:Diagnostics;jobId:string}) {
  const availableLayers=savedLayers(data)
  const [index,setIndex]=useState(0)
  const [layer,setLayer]=useState<Layer>(()=>availableLayers.find(id=>id!=='original')??'original')
  const [detectionThreshold,setDetectionThreshold]=useState(0)
  const [poseThreshold,setPoseThreshold]=useState(.45)
  const [imageError,setImageError]=useState(false)
  const [loaded,setLoaded]=useState(false)
  if(!Array.isArray(data.frames)||!data.frames.length)return <p role="status">В результате нет сохранённых кадров для диагностики.</p>
  const selected=Math.min(index,data.frames.length-1)
  const frame=data.frames[selected]
  const available=layerAvailable(layer,frame,data,selected)
  const anchor=anchorFrame(data)
  const missingLayers=layers.filter(([id])=>!availableLayers.includes(id)).map(([,label])=>label.toLocaleLowerCase('ru-RU'))
  function chooseFrame(next:number){setIndex(next);setImageError(false);setLoaded(false)}
  return <>
    <div className="diagnostic-layers" role="group" aria-label="Слой модели">{layers.filter(([id])=>availableLayers.includes(id)).map(([id,label])=><button type="button" key={id} aria-pressed={layer===id} onClick={()=>setLayer(id)}>{label}</button>)}</div>
    {missingLayers.length>0&&<p className="diagnostic-note">В этой проверке нет сохранённых слоёв: {missingLayers.join(', ')}.</p>}
    <div className="diagnostic-toolbar">
      <label>Кадр <select value={selected} onChange={e=>chooseFrame(Number(e.target.value))}>{data.frames.map((f,i)=><option value={i} key={i}>{i+1} · {f.time_sec.toFixed(2)} с</option>)}</select></label>
      <span>{modelNames[data.pipeline??'']?.[layer]??layers.find(([id])=>id===layer)?.[1]}</span>
      {(layer==='pose'||layer==='detection')&&<label htmlFor="detection-threshold">Порог детектора <input id="detection-threshold" type="range" min="0" max="1" step="0.01" value={detectionThreshold} onChange={e=>setDetectionThreshold(Number(e.target.value))}/><output>{detectionThreshold.toFixed(2)}</output></label>}
      {layer==='pose'&&<label htmlFor="pose-threshold">Порог точек позы <input id="pose-threshold" type="range" min="0" max="1" step="0.01" value={poseThreshold} onChange={e=>setPoseThreshold(Number(e.target.value))}/><output>{poseThreshold.toFixed(2)}</output></label>}
      {(layer==='pose'||layer==='detection')&&detectionThreshold>0&&<button type="button" className="diagnostic-reset" onClick={()=>setDetectionThreshold(0)}>Показать все детекции</button>}
    </div>
    {layer==='segmentation'&&Array.isArray(data.scene?.masks)&&<p className="diagnostic-note">Маски вычислены только для опорного кадра; на другие моменты они не переносятся. {anchor>=0&&selected!==anchor&&<button type="button" onClick={()=>chooseFrame(anchor)}>Открыть опорный кадр</button>}</p>}
    {!available&&<p role="status" className="diagnostic-note">{layer==='segmentation'&&anchor>=0&&selected!==anchor?'В этом кадре сегментация не выполнялась.':'Этот слой не сохранён для выбранного кадра.'}</p>}
    <figure className="diagnostic-figure">
      <div className="diagnostic-image" style={{aspectRatio:`${frame.width} / ${frame.height}`,'--frame-ratio':frame.width/frame.height} as CSSProperties}>
        <img key={selected} src={`/ui/api/jobs/${encodeURIComponent(jobId)}/diagnostics/frames/${selected}`} alt={`Исходный кадр ${timecode(frame.time_sec)}`} onLoad={()=>setLoaded(true)} onError={()=>{setImageError(true);setLoaded(false)}}/>
        {loaded&&available&&<Overlay layer={layer} frame={frame} data={data} detectionThreshold={detectionThreshold} poseThreshold={poseThreshold}/>}
      </div>
      <figcaption>{frame.time_sec.toFixed(2)} с · кадр {selected+1} из {data.frames.length} · {frame.width} × {frame.height}</figcaption>
    </figure>
    {imageError&&<p role="alert">Не удалось загрузить исходный кадр. Обновите панель или повторите позже.</p>}
    <div className="diagnostic-summary" aria-live="polite">{available&&<LayerSummary layer={layer} frame={frame} data={data} threshold={detectionThreshold}/>}</div>
    {layer==='pose'&&<p className="diagnostic-note">Порог детектора фильтрует человека и связанную с ним позу. Порог точек отвечает за расположение суставов: высокая оценка точки не исключает отражение.</p>}
    <p className="diagnostic-note">Пороги меняют только отображение. Изначально показаны все сохранённые детекции. ID трека обозначает связь детекций, а не личность человека. {data.pipeline==='fusion-v1'&&'Состояния SigLIP2 не откалиброваны и сами по себе не подтверждают нарушение.'}</p>
  </>
}

function Overlay({layer,frame,data,detectionThreshold,poseThreshold}:{layer:Layer;frame:DiagnosticFrame;data:Diagnostics;detectionThreshold:number;poseThreshold:number}) {
  const ref=useRef<SVGSVGElement>(null)
  const [scale,setScale]=useState(1)
  useEffect(()=>{
    const svg=ref.current
    if(!svg)return
    // Keep labels and joints readable when a high-resolution frame is reduced.
    const resize=()=>{const width=svg.getBoundingClientRect().width;if(width>0)setScale(frame.width/width)}
    resize()
    const observer=new ResizeObserver(resize)
    observer.observe(svg)
    return ()=>observer.disconnect()
  },[frame.width])
  return <svg ref={ref} className="diagnostic-overlay" viewBox={`0 0 ${frame.width} ${frame.height}`} style={{'--label-size':`${12*scale}px`,'--label-stroke':`${3*scale}px`} as CSSProperties} aria-hidden="true">
    {layer==='detection'&&visibleDetections(frame,detectionThreshold).map((d,i)=>{const [x,y,u,v]=d.box_xyxy;return <g key={i}><rect x={x} y={y} width={Math.max(0,u-x)} height={Math.max(0,v-y)} className="diagnostic-box"/><text x={x+3} y={Math.max(16,y-5)}>{d.label} · {Math.round(d.score*100)}%{d.track_id!=null?` · ${d.track_id}`:''}</text></g>})}
    {layer==='pose'&&visiblePoses(frame,detectionThreshold,data.pipeline).map((pose,i)=><g key={i}>{BODY_EDGES.filter(([a,b])=>visiblePoint(pose.keypoints[a],poseThreshold)&&visiblePoint(pose.keypoints[b],poseThreshold)).map(([a,b])=><line key={`${a}-${b}`} x1={pose.keypoints[a][0]} y1={pose.keypoints[a][1]} x2={pose.keypoints[b][0]} y2={pose.keypoints[b][1]} className="diagnostic-bone"/>)}{(data.pipeline==='legacy-supported'?pose.keypoints.slice(0,17):pose.keypoints).map((point,j)=>visiblePoint(point,poseThreshold)?<circle key={j} cx={point[0]} cy={point[1]} r={3*scale} className="diagnostic-joint"/>:null)}</g>)}
    {layer==='segmentation'&&data.scene?.masks?.map((mask,i)=><g key={i}>{mask.polygons.map((polygon,j)=><polygon key={j} points={polygon.map(([x,y])=>`${x*frame.width},${y*frame.height}`).join(' ')} className={`diagnostic-mask mask-${i%3}`}/>)}</g>)}
    {layer==='states'&&frame.states?.map((state,i)=>{const [x,y,u,v]=state.box_xyxy;return <g key={i}><rect x={x} y={y} width={Math.max(0,u-x)} height={Math.max(0,v-y)} className="diagnostic-box"/><text x={x+3} y={Math.max(16,y-5)}>{state.label} · {state.value}</text></g>})}
  </svg>
}
function LayerSummary({layer,frame,data,threshold}:{layer:Layer;frame:DiagnosticFrame;data:Diagnostics;threshold:number}) {
  if(layer==='original')return <>Исходный кадр без наложений.</>
  if(layer==='detection')return <>Показано объектов: {visibleDetections(frame,threshold).length} из {frame.detections?.length}. {frame.detections?.length===0?'Модель выполнена: детекций нет.':visibleDetections(frame,threshold).length===0?'Все детекции скрыты порогом. Нажмите «Показать все детекции».':''}</>
  if(layer==='pose')return <>Оценок позы: {visiblePoses(frame,threshold,data.pipeline).length} из {frame.poses?.length}. {frame.poses?.length===0?'Слой сохранён: поз нет.':'Скелет тела и точки с оценкой выше выбранного порога.'}</>
  if(layer==='segmentation')return <>Масок: {data.scene?.masks?.length}. {data.scene?.masks?.map(m=>data.scene?.objects?.find(o=>o.id===m.object_id)?.label??m.object_id).join(', ')}</>
  return <>Оценок состояния: {frame.states?.length}. {frame.states?.map(s=>`${s.label}: ${s.value}`).join('; ')}</>
}
