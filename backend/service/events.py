import base64
import math
from difflib import SequenceMatcher

from analyze import CATALOG

NB231 = "https://adilet.zan.kz/rus/docs/V1900019680"
NB14 = "https://adilet.zan.kz/rus/docs/V2000020075"
RULES = {
    "V02": ("НБРК №231, п.12", NB231, "Применимо к хранению личных вещей внутри кассового узла."),
    "V03": ("НБРК №231, п.22", NB231, "Необходимо установить незавершённое обслуживание клиента при уходе."),
    "V04": ("НБРК №231, п.20, абз.1", NB231, "Применимо к приёму и пересчёту наличных клиента в его присутствии."),
    "V05": ("НБРК №231, п.20, абз.2", NB231, "Применимо к хранению ранее принятых денег; текущая операция требует отдельного контекста."),
    "V08": ("НБРК №231, п.19, подп.1", NB231, "Проверка может выполняться с документом или через цифровой сервис; пропуск в кадрах не доказывает её отсутствие."),
    "V10": ("НБРК №231, п.24", NB231, "Проверяются сопроводительные документы, целостность и пломбирование сумки; требуется видимость процедуры."),
    "V13": ("НБРК №14, п.8 и п.16", NB14, "Нормы об устройстве кассового узла; правомерность доступа определяется отдельно."),
    "V15": ("НБРК №14, п.16", NB14, "Косвенная связь с оборудованием передаточным устройством; самостоятельный запрет передачи из рук в руки не установлен."),
    "safe_key": ("НБРК №231, п.80", NB231, "Отсылка к внутреннему порядку работы с ключами, исключающему их несанкционированное использование."),
    "safe_lock": ("НБРК №231, п.20, абз.2", NB231, "Норма относится к хранению ранее принятых денег в запирающихся устройствах; состояние замка и содержимое требуют проверки."),
    "safe_open": ("НБРК №231, п.20, абз.2", NB231, "Норма относится к хранению ранее принятых денег; открытая дверца во время текущей работы сама по себе недостаточна."),
}


def window_bounds(duration, length=12, overlap=2):
    if not math.isfinite(duration) or duration <= 0 or not 0 <= overlap < length:
        raise ValueError("Invalid temporal window configuration")
    windows, start = [], 0
    while start < duration:
        end = min(start+length, duration)
        windows.append((start, end))
        if end == duration:
            break
        start += length-overlap
    return windows


def merge_events(events):
    kept = []
    for event in sorted(events, key=lambda item: item["time_sec"]):
        duplicate = any(
            other["type"] == event["type"] and abs(other["time_sec"]-event["time_sec"]) <= 2
            and SequenceMatcher(None, other.get("visible_evidence", ""), event.get("visible_evidence", "")).ratio() >= 0.65
            for other in kept)
        if not duplicate:
            kept.append(event)
    return kept


def public_violation(event, frames, run):
    closest = min(range(len(frames)), key=lambda i: abs(frames[i]["time_sec"]-event["time_sec"]))
    indices = sorted({max(0, closest-1), closest, min(len(frames)-1, closest+1)})
    images = []
    for index in indices:
        frame = frames[index]
        images.append({"timestamp_sec": frame["time_sec"],
                       "image_base64": base64.b64encode((run/frame["image"]).read_bytes()).decode("ascii")})
    code = event["type"]
    description = describe_event(event)
    return {"type": CATALOG.get(code, code),
            "timecode_sec": frames[closest]["time_sec"],
            "description": description, "evidence_frames": images}


def describe_event(event):
    code = event["type"]
    description = event["visible_evidence"].strip()+" "+event.get("explanation", "").strip()
    if code in RULES:
        title, url, conditions = RULES[code]
        description += f" Нормативная опора: {title}; {conditions} {url}"
    else:
        description += " Конкретное нормативное основание для этого кандидата не установлено."
    return description
