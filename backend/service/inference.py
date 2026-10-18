"""Persistent local models; full-clip perception followed by overlapping VLM windows."""
import json
import math
import time
from pathlib import Path

from analyze import CATALOG, compact_evidence, evenly_spaced, parse_output
from .config import ROOT
from .events import merge_events, public_violation, window_bounds
from .primary_event import select_primary_event


class InferenceEngine:
    def __init__(self, settings):
        from models_local import PerceptionModels
        from vlm_local import LocalVLM

        self.settings = settings
        tick = time.monotonic()
        self.perception = PerceptionModels(settings.fps, 0.22, "cpu", tiles=False)
        self.vlm = LocalVLM()
        self.load_sec = time.monotonic()-tick

    def analyze(self, job, *, mode="supported", check_cancelled=None):
        if mode not in {"baseline", "supported"}:
            raise ValueError("Unknown inference mode")
        from PIL import Image
        from perception import process_video

        if check_cancelled is not None:check_cancelled()
        started = time.monotonic()
        received = job.get('metadata', {}).get('received_at', job.get('created', time.time()))
        deadline = started+self.settings.max_job_sec-max(0, time.time()-received)
        if deadline <= started:
            raise TimeoutError('Analysis time budget expired in queue')
        directory = Path(job["input_path"]).parent
        run = directory/f"attempt_{job['attempts']}"
        output, evidence, metrics = process_video(Path(job["input_path"]), self.perception if mode == "supported" else None,
            output=run, fps=self.settings.fps, deadline=deadline, check_cancelled=check_cancelled)
        if evidence["sampling"]["coverage"] != "full_clip":
            raise RuntimeError("Incomplete video coverage")
        duration = job["metadata"]["duration_sec"]
        windows = window_bounds(duration, self.settings.window_sec, self.settings.overlap_sec)
        all_events, uncertainties, window_metrics = [], [], []
        for index, (start, end) in enumerate(windows):
            if check_cancelled is not None:check_cancelled()
            if time.monotonic() >= deadline:
                raise TimeoutError("Analysis time budget exceeded")
            frames = [frame for frame in evidence["frames"] if start <= frame["time_sec"] <= end]
            if not frames:
                raise RuntimeError("Temporal window has no decoded frames")
            selected = evenly_spaced(frames, self.settings.frame_count)
            images = []
            for frame in selected:
                with Image.open(run/frame["image"]) as image:
                    images.append(image.convert("RGB"))
            support = compact_evidence({**evidence, "frames": frames,
                "signals": []})  # Cross-window summaries are not assigned to the wrong window.
            prompt = (
                "Проверь последовательность кадров банковской кассы на признаки возможных нарушений. "
                "Это часть ролика, а не полная процедура. Метки организаторов неизвестны. "
                "Итоговая задача — найти одно основное нарушение во всём ролике либо его отсутствие. "
                "Сейчас собери только обоснованных кандидатов для последующего итогового выбора; "
                "искать нарушение в каждом окне не требуется. "
                "Обычный пересчёт денег, работа с документами и временно открытый сейф во время работы "
                "не являются сами по себе нарушением. Нельзя утверждать отсутствие проверки документов, "
                "пломб, пересчёта или запирания, если шаг мог остаться вне кадров. Отделяй клиентскую "
                "зону от кабинета кассира. Измерения detector/tracker/pose могут ошибаться; проверяй их "
                "по изображениям. Пропуск трека не доказывает уход, близость руки не доказывает контакт. "
                "Не определяй личность, намерения и невидимые банковские проводки. Каталог кандидатов: "
                + json.dumps(CATALOG, ensure_ascii=False)
                + f". Окно [{start:.3f}, {end:.3f}] секунд от начала исходного видео. "
                "time_sec возвращай только в этой исходной временной шкале внутри окна. Времена кадров: "
                + json.dumps([f["time_sec"] for f in selected])
                + ((". Неуверенные измерения: " + json.dumps(support, ensure_ascii=False))
                   if mode == "supported" else "")
                + '. Ответ строго JSON: {"verdict":"violation|no_violation",'
                '"events":[{"type":"код из каталога или название нового события",'
                '"time_sec":число,"visible_evidence":"что видно",'
                '"explanation":"почему это кандидат","confidence":"low|medium|high"}],'
                '"uncertainties":["ограничения"]}. '
                "При отсутствии обоснованного события верни no_violation и пустой events."
            )
            (run/f"window_{index:03d}_input.json").write_text(json.dumps(
                {"start_sec": start, "end_sec": end, "images": [f["image"] for f in selected],
                 "prompt": prompt}, ensure_ascii=False, indent=2)+"\n")
            raw, timing = self.vlm.generate(images, prompt, max_new_tokens=1000, deadline=deadline, check_cancelled=check_cancelled)
            (run/f"window_{index:03d}_raw.txt").write_text(raw)
            result = parse_output(raw)
            if (result["verdict"] == "violation") != bool(result["events"]):
                raise ValueError("VLM verdict disagrees with its event list")
            for event in result["events"]:
                timestamp = event.get("time_sec")
                if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or not start <= timestamp <= end:
                    raise ValueError("VLM event time is outside its temporal window")
                if not isinstance(event.get("type"), str) or not isinstance(event.get("visible_evidence"), str):
                    raise ValueError("Invalid VLM event fields")
                all_events.append(event)
            uncertainties.extend(str(item) for item in result["uncertainties"])
            (run/f"window_{index:03d}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
            window_metrics.append({"start_sec": start, "end_sec": end, **timing})
        if check_cancelled is not None:check_cancelled()
        events = merge_events(all_events)
        events,selection_metrics = select_primary_event(events,evidence['frames'],run,self.vlm,
            deadline=deadline,check_cancelled=check_cancelled)
        violations = [public_violation(event, evidence["frames"], run) for event in events]
        warnings = list(dict.fromkeys([
            "Результат — кандидаты для проверки человеком, не окончательное юридическое заключение.",
            "Предметные детекции ограничены; поза и треки не подтверждают контакт или состояние замка.",
            "Проверка использует выборку кадров; короткие и невидимые действия могут быть пропущены.",
            *uncertainties]))
        metrics = {"processing_sec": time.monotonic()-started,
                   "models_loaded_once": True, "model_load_sec": self.load_sec,
                   "perception": metrics, "windows": window_metrics,
                   "primary_event_selection": selection_metrics,
                   "source_labels_used": False, "full_clip_covered": True}
        (directory/"processing_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2)+"\n")
        return {"verdict": "violation" if violations else "no_violation",
                "violations": violations, "warnings": warnings}
