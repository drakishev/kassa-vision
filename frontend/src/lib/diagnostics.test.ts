import {describe,it,expect} from 'vitest'
import {anchorFrame,layerAvailable,poseDetection,savedLayers,visibleDetections,visiblePoses,visiblePoint,type DiagnosticFrame,type Diagnostics} from './diagnostics'
const data:Diagnostics={job_id:'synthetic',frames:[{time_sec:0,width:100,height:100,detections:[],poses:[]},{time_sec:1,width:100,height:100}],scene:{anchor_time_sec:0,masks:[]}}
describe('saved model layers',()=>{
  it('distinguishes an executed empty layer from unavailable',()=>{expect(layerAvailable('detection',data.frames[0],data,0)).toBe(true);expect(layerAvailable('detection',data.frames[1],data,1)).toBe(false)})
  it('never propagates anchor masks to later frames',()=>{expect(anchorFrame(data)).toBe(0);expect(layerAvailable('segmentation',data.frames[0],data,0)).toBe(true);expect(layerAvailable('segmentation',data.frames[1],data,1)).toBe(false)})
  it('rejects low confidence or nonfinite pose points',()=>{expect(visiblePoint([10,20,.8],.45)).toBe(true);expect(visiblePoint([10,20,.2],.45)).toBe(false);expect(visiblePoint([NaN,20,1],.45)).toBe(false);expect(visiblePoint(undefined,.45)).toBe(false)})
  it('offers only saved layers, including executed empty layers',()=>{
    expect(savedLayers(data)).toEqual(['original','detection','pose','segmentation'])
    expect(savedLayers({...data,scene:undefined})).toEqual(['original','detection','pose'])
  })
  it('filters a pose by its parent detection, separately from joint scores',()=>{
    const frame:DiagnosticFrame={time_sec:0,width:100,height:100,
      detections:[{label:'person',score:.3,box_xyxy:[0,0,20,60],track_id:'weak'},{label:'person',score:.8,box_xyxy:[30,0,90,100],track_id:'strong'}],
      poses:[{track_id:'strong',keypoints:[[40,20,.9]]},{track_id:'weak',keypoints:[[10,20,.99]]}]}
    expect(visibleDetections(frame,0)).toHaveLength(2)
    expect(visiblePoses(frame,0)).toHaveLength(2)
    expect(visiblePoses(frame,.45).map(p=>p.track_id)).toEqual(['strong'])
    expect(visiblePoint(frame.poses![1].keypoints[0],.45)).toBe(true)
  })
  it('recovers RTMW links before track IDs are assigned without changing detection order',()=>{
    const frame:DiagnosticFrame={time_sec:0,width:100,height:100,
      detections:[{label:'person',score:.3,box_xyxy:[0,0,20,60],track_id:null},{label:'person',score:.8,box_xyxy:[30,0,90,100],track_id:null}],
      poses:[{track_id:null,keypoints:[[40,20,.9]]},{track_id:null,keypoints:[[10,20,.99]]}]}
    expect(poseDetection(frame,0,'legacy-supported')).toBe(frame.detections![1])
    expect(visiblePoses(frame,.45,'legacy-supported')).toEqual([frame.poses![0]])
    expect(frame.detections![0].score).toBe(.3)
    expect(poseDetection(frame,0,'fusion-v1')).toBeUndefined()
    expect(poseDetection({...frame,poses:frame.poses!.slice(0,1)},0,'legacy-supported')).toBeUndefined()
  })
})
