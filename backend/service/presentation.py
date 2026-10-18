"""UI-only details restored from saved local inference artifacts.

The judge API and persisted result are never changed. A reason is assigned only
when its type, evidence time and full rendered description match unambiguously.
"""
import json
import math
import sqlite3
from pathlib import Path

from analyze import CATALOG
from .events import NB14, NB231, RULES, describe_event, merge_events

CLASS_TITLES = {"V05": "Деньги оставлены на рабочем столе"}

DOCUMENT_TITLES = {
    NB231: 'Правила осуществления кассовых операций и операций по инкассации банкнот, монет и ценностей в банках второго уровня, филиалах банков-нерезидентов РК, Национальном операторе почты и юридических лицах, исключительной деятельностью которых является инкассация банкнот, монет и ценностей (НБРК от 29.11.2019 № 231)',
    NB14: 'Правила организации охраны и устройства помещений банков второго уровня, филиалов банков-нерезидентов РК, Национального оператора почты, инкассаторских организаций и обменных пунктов (НБРК от 24.02.2020 № 14)',
}


def _catalog_code(event_type):
    if event_type in CATALOG:return event_type
    return next((code for code,name in CATALOG.items() if name==event_type),None)


def _saved_events(settings,job_id):
    with sqlite3.connect(f'file:{settings.database}?mode=ro',uri=True) as db:
        row=db.execute('SELECT input_path,attempts FROM jobs WHERE job_id=?',(job_id,)).fetchone()
    if row is None:return []
    directory=Path(row[0]).resolve(strict=True).parent
    if not directory.is_relative_to((settings.runtime/'jobs').resolve()):return []
    run=directory/f'attempt_{row[1]}'
    # Fusion stores final, densely rechecked times directly; do not snap them to the 2fps survey.
    accepted=run/'accepted.json'
    if accepted.exists():
        return [{'type':CATALOG.get(event['type'],event['type']),
                 'timecode_sec':event['time_sec'],'description':describe_event(event),'event':event}
                for event in json.loads(accepted.read_text())['events']]
    frames=json.loads((run/'evidence.json').read_text())['frames']
    times=[frame['time_sec'] for frame in frames]
    if not times or not all(isinstance(t,(float,int)) and math.isfinite(t) for t in times):return []
    events=[]
    for file in sorted(run.glob('window_[0-9][0-9][0-9].json')):
        events.extend(json.loads(file.read_text())['events'])
    saved=[]
    for event in merge_events(events):
        saved.append({'type':CATALOG.get(event['type'],event['type']),
                      'timecode_sec':min(times,key=lambda t:abs(t-event['time_sec'])),
                      'description':describe_event(event),
                      'event':event})
    return saved


def enrich_review(settings,job_id,result):
    if result.get('status')!='done' or result.get('verdict')!='violation':return result
    try:
        saved=_saved_events(settings,job_id)
    except (OSError,sqlite3.Error,ValueError,KeyError,TypeError,AttributeError):
        # Legacy or missing artifacts must not hide the original result.
        saved=[]
    violations=[]
    for violation in result['violations']:
        code=_catalog_code(violation['type'])
        matches=[item['event'] for item in saved if
                 item['type']==violation['type'] and
                 abs(item['timecode_sec']-violation['timecode_sec'])<1e-6 and
                 item['description']==violation['description']]
        event=matches[0] if len(matches)==1 else {}
        if event:code=_catalog_code(event['type'])
        normative=None
        if code in RULES:
            reference,url,conditions=RULES[code]
            normative={'document_title':DOCUMENT_TITLES[url],'reference':reference,
                       'url':url,'conditions':conditions}
        review={'classification':{'code':code,'name':CATALOG.get(code,violation['type']),
                                  'in_catalog':code is not None,
                                  'title':CLASS_TITLES.get(code,CATALOG.get(code,violation['type']))},
                'observation':event.get('visible_evidence','').strip() or None,
                'reason':event.get('explanation','').strip() or None,
                'normative':normative}
        violations.append({**violation,'review':review})
    return {**result,'violations':violations}
