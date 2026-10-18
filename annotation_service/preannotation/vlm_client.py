"""Same local 27B checkpoint via the installed optimized vLLM server."""
import os,base64,hashlib,io,json,time,urllib.request
from pathlib import Path
from urllib.parse import urlsplit
class LocalVLMClient:
 def __init__(self,url):
  if urlsplit(url).hostname not in ('127.0.0.1','localhost','::1'):raise ValueError('Only loopback inference is allowed')
  self.url=url.rstrip('/')
  config=Path(os.environ.get('KASSA_VLM_CHECKPOINT',Path(__file__).resolve().parents[2]/'backend/models/vlm'))/'config.json'
  self.cache_identity={'backend':'local_vllm','model':'annotation-27b','model_config_sha256':hashlib.sha256(config.read_bytes()).hexdigest() if config.exists() else None,'temperature':0,'seed':42,'enable_thinking':False}
 def generate(self,images,prompt,max_new_tokens=2400,deadline=None):
  content=[]
  for im in images:
   stream=io.BytesIO();im.save(stream,format='PNG');content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()}})
  content.append({'type':'text','text':prompt})
  payload={'model':'annotation-27b','messages':[{'role':'user','content':content}],'temperature':0,'max_tokens':max_new_tokens,'seed':42,'chat_template_kwargs':{'enable_thinking':False}}
  req=urllib.request.Request(self.url+'/v1/chat/completions',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
  tick=time.monotonic()
  with urllib.request.urlopen(req,timeout=max(1,(deadline-tick) if deadline else 300)) as response:result=json.load(response)
  if result['choices'][0].get('finish_reason')=='length':raise ValueError('Model output truncated at token limit')
  return result['choices'][0]['message']['content'],{'runtime_sec':time.monotonic()-tick,'output_tokens':result.get('usage',{}).get('completion_tokens'),'backend':'local_vllm','model':'annotation-27b'}
