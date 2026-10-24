"""Exceptional event-gap recovery for the bounded UI mirror, never request replay.

All helpers are called while the owning LocalApp lock is held. Coalescing a
replaceable progress/activity event is deliberately not considered data loss.
"""
from copy import deepcopy

EVENT_LIMIT = 300
STREAM_LIMIT = 16
TEXT_LIMIT = 100000
TOTAL_TEXT_LIMIT = 500000


def reset_display(item):
    """Use whenever a session receives a fresh seq/events timeline."""
    for key in ('_eventsDiscardedThrough', '_displayStreams', '_displayError', '_displayStatusLabel'):
        item.pop(key, None)
    for message in item.get('messages', []):
        message.pop('_eventAnchor', None)


def trim_events(item):
    events = item['events']
    if len(events) > EVENT_LIMIT:
        removed = events[:-EVENT_LIMIT]
        item['_eventsDiscardedThrough'] = max(item.get('_eventsDiscardedThrough', 0), removed[-1]['seq'])
        item['events'] = events[-EVENT_LIMIT:]


def needs_recovery(item, after):
    return after < item.get('_eventsDiscardedThrough', 0) or after > item['seq']


def track_display_event(item, kind, data):
    """Keep only unfinished display text, under the existing browser bounds."""
    if kind in {'connection_idle', 'connection_restart_finished'}:
        item.pop('_displayStreams', None)
    if kind == 'connected':
        item.pop('_displayError', None)
    if kind == 'status':
        item['_displayStatusLabel'] = str(data.get('label') or '')[:2000]
        if data.get('state') == 'starting':
            item.pop('_displayError', None)
            item.pop('_displayStreams', None)
    elif kind == 'error':
        item['_displayError'] = str(data.get('message') or '')[:10000]
        item.pop('_displayStatusLabel', None)
    elif kind == 'result':
        item.pop('_displayError', None)
        item.pop('_displayStatusLabel', None)
    if kind not in {'assistant', 'assistant_delta'}:
        return
    identifier = data.get('messageId')
    if not isinstance(identifier, str) or not identifier or len(identifier) > 200:
        return
    index = data.get('index', 0)
    if type(index) is not int or index < 0:
        return
    key = (identifier, index)
    streams = item.setdefault('_displayStreams', {})
    if kind == 'assistant':
        streams.pop(key, None)
        if not streams:
            item.pop('_displayStreams', None)
        return
    row = streams.get(key)
    if row is None:
        # A provider may issue many independent blocks; match the existing
        # coalesced browser buffer's block limit instead of retaining forever.
        if len(streams) >= STREAM_LIMIT:
            streams.pop(next(iter(streams)))
        messages = item.get('messages', [])
        anchor = messages[-1].setdefault('_eventAnchor', item['seq'] + 1) if messages else None
        row = streams[key] = {'messageId': identifier, 'index': index,
            'runId': data.get('runId'), '_anchor': anchor,
            '_chunks': [], '_size': 0, 'uiTruncated': False}
    incoming = str(data.get('text') or '')
    available = min(TEXT_LIMIT - row['_size'],
                    TOTAL_TEXT_LIMIT - sum(value['_size'] for value in streams.values()))
    addition = incoming[:max(0, available)] if not row['uiTruncated'] else ''
    if addition:
        row['_chunks'].append(addition)
        row['_size'] += len(addition)
        # Avoid thousands of tiny chunk objects without copying a growing
        # 100k response on every single token.
        if len(row['_chunks']) >= 64:
            row['_chunks'] = [''.join(row['_chunks'])]
    row['uiTruncated'] |= bool(data.get('uiTruncated')) or len(incoming) > available


def display_streams(item):
    messages = item.get('messages', [])
    positions = {message['_eventAnchor']: index for index, message in enumerate(messages) if '_eventAnchor' in message}
    return [{key: row[key] for key in ('messageId', 'index', 'runId', 'uiTruncated')} |
            {'text': ''.join(row['_chunks']),
             'afterMessage': positions.get(row['_anchor'], -1),
             'interrupted': item.get('state') in {'stopped', 'error'}}
            for row in item.get('_displayStreams', {}).values()]


def snapshot_recovery(item, public_session, after):
    """Freeze cursor and state together; callers do not return old deltas too."""
    session = deepcopy(public_session)
    for message in session.get('messages', []):
        message.pop('_eventAnchor', None)
    session.update(streams=display_streams(item),
                   statusLabel=item.get('_displayStatusLabel', ''),
                   displayError=item.get('_displayError', ''))
    return {'reason': 'cursor_reset' if after > item['seq'] else 'event_gap',
            'discardedThrough': item.get('_eventsDiscardedThrough', 0), 'session': session}
