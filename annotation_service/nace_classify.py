"""Classify existing textual observations; never writes the annotation database."""
import argparse
import ast
import csv
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'nace-text-scenarios-v3'
STATUS_CRITERIA = {
    'described': 'Наблюдения прямо описывают все необходимые наблюдаемые признаки сценария; это соответствие тексту, не проверка видео и не юридический вывод.',
    'uncertain': 'Есть относящийся к сценарию признак, но необходимые детали неясны, противоречивы или указаны только как гипотеза.',
    'not_described': 'Положительных признаков именно этого сценария в тексте нет, либо описано обычное действие, явно исключённое критерием. Это не доказательство отсутствия события на видео.',
}
SPECIFIC_REQUIREMENTS = {
    'safe_lock': 'Для described необходимо прямое наблюдение, что кассир НЕ запирает сейф '
                 'при уходе, с видимой полной процедурой и видимым замком. Если замок или '
                 'рука перекрыты, поворот ключа не наблюдался либо сказано «неизвестно, '
                 'заперт ли сейф», ответ строго uncertain. Закрытие дверцы и уход не '
                 'подтверждают отсутствие запирания.',
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def questions_for(catalog):
    return {code: {'type': 'choice',
                   'instructions': 'Определи, подтверждает ли текст наблюдений именно этот сценарий. '
                                   'Не додумывай пропущенные действия, не путай клиента с кассиром. '
                                   'Гипотеза не факт; отсутствие упоминания не доказательство отсутствия действия. '
                                   + SPECIFIC_REQUIREMENTS.get(code, ''),
                   'criteria': {
                       'described': 'Текст явно подтверждает весь сценарий: ' + description,
                       'uncertain': 'Есть относящийся к следующему сценарию признак, но неясна важная деталь: ' + description,
                       'not_described': 'Именно следующий сценарий не описан, опровергнут или описано лишь обычное действие: ' + description,
                   }}
            for code, description in catalog.items()}


def load_records(labels_path, database_path):
    """Prefer current episode annotations; exclude prior class and organizer verdict."""
    labels = json.loads(labels_path.read_text(encoding='utf-8-sig'))
    if not isinstance(labels, list) or not labels:
        raise ValueError('Expected a nonempty list of text annotations')
    working = {}
    if database_path is not None:
        uri = database_path.resolve().as_uri() + '?mode=ro'
        with sqlite3.connect(uri, uri=True) as db:
            working = {vid: (revision, json.loads(body), updated)
                       for vid, revision, body, updated in db.execute(
                           'SELECT video_id, revision, body, updated_at FROM annotations')}
    vocabulary = {}
    for node in ast.parse((ROOT / 'annotation_service/schema.py').read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in ('ACTIONS', 'ITEMS', 'ROLES', 'VISIBILITY'):
                vocabulary[node.targets[0].id] = ast.literal_eval(node.value)
    records, seen = [], set()
    for row in labels:
        filename = row['filename']
        if not isinstance(filename, str) or Path(filename).name != filename or filename in seen:
            raise ValueError('Invalid or duplicate annotation filename')
        seen.add(filename)
        video_id = hashlib.sha256(filename.encode()).hexdigest()[:20]
        revision, annotation, updated = working.get(video_id, (0, {}, None))
        if annotation.get('episodes'):
            # IDs, roles, timing and notes are preserved; names and reviewer identity are omitted.
            state = {
                'source': 'working_human_episode_annotations',
                'vocabulary': vocabulary,
                'full_video_reviewed': annotation.get('full_video_reviewed', False),
                'review_status': annotation.get('status', 'unreviewed'),
                'participants': [{k: p.get(k, '') for k in ('id', 'role')}
                                 for p in annotation.get('participants', [])],
                'episodes': [{k: e.get(k, '') for k in
                              ('id', 'start', 'end', 'action', 'item', 'actor_id',
                               'recipient_id', 'visibility', 'note')}
                             for e in annotation['episodes']],
                'notes': annotation.get('notes', ''),
                'unverified_hypotheses': [e['hypothesis'] for e in annotation['episodes']
                                          if e.get('hypothesis')],
                'annotator_interpretation': annotation.get('label_check', {}).get('text', ''),
            }
            provenance = {'source': 'working_human_episode_annotations', 'video_id': video_id,
                          'revision': revision, 'updated_at': updated,
                          'annotation_sha256': digest(annotation)}
        else:
            state = {k: row.get(k, '') for k in
                     ('observed_action', 'limitations', 'description_source',
                      'full_video_reviewed', 'video_independently_verified')}
            state['source'] = 'reference_text_annotation'
            provenance = {'source': state['source'], 'description_source': row.get('description_source', ''),
                          'row_sha256': digest(row)}
        records.append({'filename': filename, 'state': state, 'provenance': provenance})
    return records


def write_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    tmp.replace(path)


def save_report(output, records, results, catalog):
    completed = [r for r in results if r['status'] == 'completed']
    checks = [{'id': r['filename'], 'code': code, 'expected': expected,
               'actual': r['response']['answers'][code]['choice'],
               'passed': r['response']['answers'][code]['choice'] == expected}
              for r in completed for code, expected in r.get('expected', {}).items()]
    summary = {
        'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
        'input_count': len(records), 'completed': len(completed),
        'failed': sum(r['status'] == 'failed' for r in results),
        'sources': dict(Counter(r['provenance']['source'] for r in results)),
        'input_tokens': sum(r['response'].get('usage', {}).get('input_tokens', 0) for r in completed),
        'semantic_controls': checks,
        'interpretation': 'Text-to-scenario proposals only. Probabilities are model outputs, not measured accuracy. '
                          'No video was sent or independently verified. No legal verdict or training labels created.',
        'human_review_required': True,
    }
    write_json(output / 'results.json', {'summary': summary, 'catalog': catalog, 'results': results})
    with (output / 'classifications.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['filename', 'source', 'code', 'status', 'probability', 'description'])
        for row in completed:
            for code, answer in row['response']['answers'].items():
                writer.writerow([row['filename'], row['provenance']['source'], code,
                                 answer['choice'], answer['probabilities'][answer['choice']], catalog[code]])
    lines = ['# Nace: классификация текстовых аннотаций', '',
             f"Обработано: {len(completed)}/{len(records)}. Ошибок: {summary['failed']}.", '',
             'Это предложения по содержанию текста. Видео не проверялось; юридические выводы '
             'и эталонные метки не создаются. Вероятности модели не являются измеренной точностью.', '',
             '| Запись | Источник | Описанные сценарии | Неясные сценарии |',
             '|---|---|---|---|']
    for row in completed:
        answers = row['response']['answers']
        groups = {s: ', '.join(code for code, a in answers.items() if a['choice'] == s) or '—'
                  for s in ('described', 'uncertain')}
        name = row['filename'].replace('|', '\\|').replace('\n', ' ')
        lines.append(f"| {name} | {row['provenance']['source']} | {groups['described']} | {groups['uncertain']} |")
    if checks:
        lines += ['', f"Семантические проверки: {sum(c['passed'] for c in checks)}/{len(checks)}.",
                  'Это небольшой набор диагностических примеров, а не оценка точности на видео.']
    lines += ['', 'Определения сценариев:', ''] + [f'- **{code}**: {text}' for code, text in catalog.items()]
    (output / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    return summary


def main():
    from .nace_client import NaceClient, NaceError, validate_answers

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--labels', type=Path, default=ROOT / 'data/dataset/reference/labels_final.json')
    parser.add_argument('--database', type=Path, default=ROOT / 'data/annotation_service/annotations.sqlite3')
    parser.add_argument('--reference-only', action='store_true', help='Use reference text instead of working episodes')
    parser.add_argument('--catalog', type=Path, default=ROOT / 'configs/nace_scenarios.json')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='drex-v2.0')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--dry-run', action='store_true', help='Save inputs only; no API calls')
    parser.add_argument('--controls', action='store_true', help='Run synthetic semantic controls instead of project data')
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error('--limit must be positive')
    catalog_document = json.loads(args.catalog.read_text())
    catalog = catalog_document['categories']
    if not isinstance(catalog, dict) or not catalog or not all(
            isinstance(k, str) and k and isinstance(v, str) and v for k, v in catalog.items()):
        parser.error('Invalid scenario catalog')
    questions = questions_for(catalog)
    if args.controls:
        controls = json.loads((ROOT / 'annotation_service/tests/fixtures/nace_controls.json').read_text())
        records = [{'filename': r['id'], 'state': r['state'], 'expected': r['expected'],
                    'provenance': {'source': 'synthetic_semantic_control'}} for r in controls]
    else:
        records = load_records(args.labels, None if args.reference_only else args.database)
    if args.limit is not None:
        records = records[:args.limit]
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / 'inputs.json', {'version': VERSION, 'catalog': catalog_document,
                                            'questions': questions, 'records': records})
    if args.dry_run:
        print(json.dumps({'prepared': len(records), 'api_calls': 0, 'output': str(args.output)}))
        return 0
    client = NaceClient.from_env(args.env_file)
    models = {m['name']: m for m in client.models()['models']}
    if args.model not in models:
        parser.error('Requested model is not listed by this API key')
    served_model = models[args.model].get('alias_for') or args.model
    calls = args.output / 'calls'
    calls.mkdir(exist_ok=True)
    results = []
    for record in records:
        payload = {'model': args.model, 'state': record['state'], 'questions': questions}
        signature = digest({'version': VERSION, 'payload': payload, 'served_model': served_model,
                            'provenance': record['provenance']})
        path = calls / (signature + '.json')
        result = {k: v for k, v in record.items() if k != 'state'}
        result['request_sha256'] = signature
        try:
            if path.exists():
                response = json.loads(path.read_text())
                validate_answers(response, questions)
                result['cached'] = True
            else:
                response = client.evaluate(payload)
                result['cached'] = False
            if response.get('model') != served_model:
                raise NaceError('Unexpected response model')
            write_json(path, response)
            result.update(status='completed', response=response)
        except (NaceError, ValueError) as exc:
            result.update(status='failed', error=str(exc))
        results.append(result)
        summary = save_report(args.output, records, results, catalog)
        print(f"{len(results)}/{len(records)} {record['filename']}: {result['status']}", flush=True)
        if result['status'] == 'failed':
            # No paid automatic retries or repeated requests after auth/rate-limit failures.
            print(result['error'], flush=True)
            break
    console_summary = {k: v for k, v in summary.items() if k != 'semantic_controls'}
    console_summary['control_checks'] = len(summary['semantic_controls'])
    console_summary['control_failures'] = sum(not c['passed'] for c in summary['semantic_controls'])
    print(json.dumps(console_summary, ensure_ascii=False), flush=True)
    return int(summary['failed'] > 0 or any(not c['passed'] for c in summary['semantic_controls']))


if __name__ == '__main__':
    raise SystemExit(main())
