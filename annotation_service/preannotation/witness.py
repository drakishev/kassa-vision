"""Synthetic-only local CUDA, SAM2 and optional loopback VLM environment witness."""
import os,argparse,json,sys
from pathlib import Path
from annotation_service.paths import dataset_path, runtime_path, source_records
import numpy as np
import torch
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[2]

def main():
    p=argparse.ArgumentParser();p.add_argument('--vlm-url');p.add_argument('--output',required=True);args=p.parse_args()
    torch.manual_seed(42);torch.set_num_threads(4)
    x=torch.randn(64,64,device='cuda',dtype=torch.bfloat16);y=x@x.T;assert torch.isfinite(y).all()
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
    im=Image.new('RGB',(512,512),'white');ImageDraw.Draw(im).rectangle((100,100,380,400),fill='red')
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
        predictor=SAM2ImagePredictor(build_sam2('configs/sam2.1/sam2.1_hiera_s.yaml',os.environ.get('ANNOTATION_SAM_CHECKPOINT',str(ROOT/'backend/models/sam2.1_hiera_small.pt')),apply_postprocessing=False))
        predictor.set_image(np.array(im));masks,scores,_=predictor.predict(box=np.array([100,100,380,400]),multimask_output=False)
    assert masks.shape==(1,512,512) and masks.any() and np.isfinite(scores).all()
    result={'synthetic_only':True,'torch':torch.__version__,'cuda':torch.version.cuda,'device':torch.cuda.get_device_name(),'kernel_finite':True,'sam_mask_shape':list(masks.shape),'sam_nonempty':True,'sam_score':float(scores[0])}
    if args.vlm_url:
        from .vlm_client import LocalVLMClient
        raw,timing=LocalVLMClient(args.vlm_url).generate([im],'Return JSON with the color of the rectangle as color.',max_new_tokens=100)
        result['vlm']=timing;result['vlm_nonempty']=bool(raw);assert raw
    from .common import write_json
    write_json(args.output,result);print(json.dumps(result))
if __name__=='__main__':main()
