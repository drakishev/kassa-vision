"""Completeness of the quick episode protocol; mirrored in static/episodes.js."""


def protocol_gaps(annotation, organizer_label=''):
    """What is still missing before a video counts toward the episode dataset."""
    gaps = []
    episodes = annotation.get('episodes', [])
    status = annotation.get('label_check', {}).get('status', 'unset')
    if not annotation.get('reviewer', '').strip():
        gaps.append('Не указан проверяющий')
    if not annotation.get('full_video_reviewed'):
        gaps.append('Видео не просмотрено целиком')
    if organizer_label == 'нарушение':
        if status == 'unset':
            gaps.append('Не отмечено, что объясняет метку организатора')
        elif status in ('explained', 'partly') and not any(e.get('explains_label') for e in episodes):
            gaps.append('Нет эпизода с отметкой «объясняет метку»')
    if not episodes:
        gaps.append('Нет ни одного эпизода (для нормального видео отметьте обычные действия)')
    if any(not e.get('actor_id') for e in episodes):
        gaps.append('У части эпизодов не указано, кто действует')
    if any(e.get('action') == 'hand_over' and not e.get('recipient_id') for e in episodes):
        gaps.append('У передачи не указано, кому')
    return gaps
