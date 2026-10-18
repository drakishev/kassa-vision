export type Detection = {label:string;score:number;box_xyxy:[number,number,number,number];track_id:string|number|null}
export type Pose = {track_id:string|number|null;keypoints:number[][]}
export type State = {label:string;value:string;box_xyxy:number[];margin?:number;calibrated?:boolean}
export type DiagnosticFrame = {frame_id?:string;time_sec:number;width:number;height:number;detections?:Detection[];poses?:Pose[];states?:State[]}
export type DiagnosticMask = {object_id:string;polygons:number[][][];source?:string;scope?:string;score?:number}
export type Diagnostics = {job_id:string;pipeline?:string;frames:DiagnosticFrame[];scene?:{anchor_time_sec?:number;masks?:DiagnosticMask[];objects?:{id:string;label:string}[]};sampling?:{target_fps?:number}}
export type Layer = 'original'|'detection'|'pose'|'segmentation'|'states'
export const BODY_EDGES = [[0,1],[0,2],[1,3],[2,4],[5,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]]
// Model confidence is a display filter; it never changes the saved analysis.
export function visiblePoint(point:number[]|undefined, threshold:number) {
  return Boolean(point && point.length>=3 && point.slice(0,3).every(Number.isFinite) && point[2]>=threshold)
}
export function anchorFrame(data:Diagnostics):number {
  const anchor=data.scene?.anchor_time_sec
  if (anchor===undefined) return -1
  return data.frames.findIndex(f=>Math.abs(f.time_sec-anchor)<.001)
}
export function layerAvailable(layer:Layer, frame:DiagnosticFrame, data:Diagnostics, index:number):boolean {
  if(layer==='original')return true
  if(layer==='segmentation')return index===anchorFrame(data) && Array.isArray(data.scene?.masks)
  return Array.isArray(frame[layer==='detection'?'detections':layer==='pose'?'poses':'states'])
}

export function savedLayers(data:Diagnostics):Layer[] {
  return (['original','detection','pose','segmentation','states'] as Layer[]).filter(layer=>
    Array.isArray(data.frames)&&data.frames.some((frame,index)=>layerAvailable(layer,frame,data,index)))
}

export function visibleDetections(frame:DiagnosticFrame, threshold:number):Detection[] {
  return frame.detections?.filter(d=>Number.isFinite(d.score)&&d.score>=threshold)??[]
}

/** A keypoint score measures joint location, not whether the crop is a real person. */
export function poseDetection(frame:DiagnosticFrame, index:number, pipeline?:string):Detection|undefined {
  const pose=frame.poses?.[index]
  if(!pose)return undefined
  const people=frame.detections?.filter(d=>d.label==='person')??[]
  if(pose.track_id!=null){
    const matches=people.filter(d=>d.track_id===pose.track_id)
    if(matches.length===1)return matches[0]
  }
  // RTMW receives the top four person boxes in score order, even before ByteTrack
  // assigns IDs. Use that producer contract only when the saved arrays agree.
  if(pipeline==='legacy-supported'){
    const inputs=[...people].sort((a,b)=>b.score-a.score).slice(0,4)
    if(inputs.length===frame.poses?.length&&inputs.every((d,i)=>d.track_id===frame.poses?.[i].track_id))return inputs[index]
  }
  return undefined
}

export function visiblePoses(frame:DiagnosticFrame, threshold:number, pipeline?:string):Pose[] {
  return frame.poses?.filter((_,index)=>{
    const detection=poseDetection(frame,index,pipeline)
    return !detection||(Number.isFinite(detection.score)&&detection.score>=threshold)
  })??[]
}
