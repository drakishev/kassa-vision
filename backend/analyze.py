"""Shared violation catalog and VLM response parsing."""
import json

CATALOG = {
    "V02": "Личные вещи на рабочей поверхности для денег",
    "V03": "Уход до завершения обслуживания клиента",
    "V04": "Пересчёт принятых денег после ухода клиента",
    "V05": "После окончания работы с деньгами наличные остаются на столе: кассир переключается на постороннее занятие, принимает следующего клиента или уходит, не убрав деньги",
    "V08": "Приём денег без видимой проверки документов — требует полноты процедуры",
    "V10": "Приём инкассаторской сумки без видимой проверки — требует полноты процедуры",
    "V13": "Вход клиента внутрь рабочего кабинета кассира",
    "V15": "Непосредственная передача наличных из рук в руки",
    "safe_open": "Открытый сейф при переключении на другую работу",
    "safe_lock": "Закрытие дверцы без видимого запирания при уходе",
    "safe_key": "Ключ оставлен в наружном замке сейфа при уходе",
    "documents_left": "Рабочие документы оставлены на столе при уходе",
}


def evenly_spaced(items, count):
    if len(items) <= count:
        return items
    return [items[round(i*(len(items)-1)/(count-1))] for i in range(count)]


def compact_evidence(data):
    frames = []
    for frame in evenly_spaced(data["frames"], 30):
        poses = [{"track_id": p["track_id"],
                  "elbows_wrists_xy_confidence": [p["keypoints"][i] for i in [7, 8, 9, 10]]}
                 for p in frame["poses"]]
        frames.append({"time_sec": frame["time_sec"],
                       "detections": frame["detections"][:12], "poses": poses,
                       "relations": frame["relations"][:8]})
    return {"sampling": data["sampling"], "frames": frames,
            "signals": data["signals"], "limitations": data["limitations"]}


def parse_output(text):
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("VLM did not return a JSON object")
    data = json.loads(text[start:end+1])
    if data.get("verdict") not in {"violation", "no_violation"}:
        raise ValueError("Invalid verdict")
    if not isinstance(data.get("events"), list) or not isinstance(data.get("uncertainties"), list):
        raise ValueError("Expected events and uncertainties arrays")
    return data
