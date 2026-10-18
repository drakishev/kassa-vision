"""Reusable offline VLM; weights load once for a worker's lifetime."""
import os
import time
from pathlib import Path


class LocalVLM:
    def __init__(self, checkpoint=None):
        checkpoint = Path(checkpoint or os.environ.get('KASSA_VLM_CHECKPOINT', Path(__file__).resolve().parent / 'models/vlm'))
        import torch
        from transformers import AutoTokenizer, Qwen3_5ForConditionalGeneration
        from transformers.models.qwen2_vl.image_processing_pil_qwen2_vl import Qwen2VLImageProcessorPil

        if not torch.cuda.is_available():
            raise RuntimeError("A CUDA GPU is required for the configured VLM")
        if torch.cuda.mem_get_info()[0] < 65*1024**3:
            raise RuntimeError("Insufficient free GPU memory for VLM; restart with another GPU")
        torch.set_num_threads(8)
        torch.manual_seed(0)
        self.torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(checkpoint, local_files_only=True)
        self.processor = Qwen2VLImageProcessorPil.from_pretrained(checkpoint, local_files_only=True)
        self.template = (checkpoint / "chat_template.jinja").read_text()
        self.model = Qwen3_5ForConditionalGeneration.from_pretrained(
            checkpoint, local_files_only=True, dtype=torch.bfloat16,
            attn_implementation="sdpa").to("cuda").eval()

    def generate(self, images, prompt, max_new_tokens=1000, deadline=None, check_cancelled=None):
        if check_cancelled is not None:check_cancelled()
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Inference time budget exceeded")
        content = [{"type": "image"} for _ in images]+[{"type": "text", "text": prompt}]
        text = self.tokenizer.apply_chat_template([{"role": "user", "content": content}],
                chat_template=self.template, tokenize=False, add_generation_prompt=True,
                enable_thinking=False)
        visual = self.processor(images=images, return_tensors="pt")
        parts = text.split("<|image_pad|>")
        if len(parts) != len(images)+1:
            raise RuntimeError("Unexpected image-token template")
        text = parts[0]
        for grid, part in zip(visual["image_grid_thw"], parts[1:]):
            text += "<|image_pad|>"*(int(grid.prod())//self.processor.merge_size**2)+part
        inputs = {k: v.to("cuda") for k, v in dict(self.tokenizer(text, return_tensors="pt"), **visual).items()}
        options = {"max_new_tokens": max_new_tokens, "do_sample": False, "use_cache": True,
                   "pad_token_id": self.tokenizer.pad_token_id or 248044}
        if deadline is not None:
            options["max_time"] = max(0.1, deadline-time.monotonic())
        if check_cancelled is not None:
            from transformers import StoppingCriteriaList
            from service.cancellation import CancellationCriteria
            check_cancelled()
            options['stopping_criteria'] = StoppingCriteriaList([CancellationCriteria(check_cancelled)])
        tick = time.monotonic()
        with self.torch.inference_mode():
            output = self.model.generate(**inputs, **options)
        if check_cancelled is not None:check_cancelled()
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("Inference time budget exceeded")
        count = inputs["input_ids"].shape[1]
        text = self.tokenizer.decode(output[0, count:], skip_special_tokens=True)
        return text, {"runtime_sec": time.monotonic()-tick,
                      "output_tokens": int(output.shape[1]-count)}
