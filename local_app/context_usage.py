"""One root model response, measured against the window reported by the CLI."""
from typing import NamedTuple


def _count(value):
    return type(value) is int and 0 <= value <= 2**31 - 1


def usage_snapshot(used, window):
    if not _count(used) or not _count(window) or not window or used > window:
        return None
    return {'usedTokens': used, 'windowTokens': window, 'percent': (100 * used + window // 2) // window}


def normalize_snapshot(value):
    return usage_snapshot(value.get('usedTokens'), value.get('windowTokens')) if isinstance(value, dict) else None


class ContextSample(NamedTuple):
    session: str
    model: str
    message_id: str | None
    used: int
    window: int | None

    def for_result(self, data):
        if self.session != data.get('session_id') or data.get('isReplay'):
            return None
        window = self.window
        if window is None:
            # inputTokens/outputTokens here are cumulative. Use only the
            # capacity of the model that produced the last root response.
            models = data.get('modelUsage')
            model = models.get(self.model) if isinstance(models, dict) else None
            window = model.get('contextWindow') if isinstance(model, dict) else None
        return usage_snapshot(self.used, window)


def capture_sample(data, message, previous=None):
    """An optional streamed start and its final assistant frame refer to one API call."""
    session = data.get('session_id')
    if not isinstance(session, str) or not 0 < len(session) <= 160 or not isinstance(message, dict):
        return None
    message_id = message.get('id')
    if not isinstance(message_id, str) or not 0 < len(message_id) <= 200:
        message_id = None
    detail = data.get('context_usage')  # Structured reply when the user invokes /context.
    if isinstance(detail, dict):
        snapshot = usage_snapshot(detail.get('totalTokens'), detail.get('maxTokens'))
        if snapshot is not None:
            return ContextSample(session, '', message_id, snapshot['usedTokens'], snapshot['windowTokens'])
    model = message.get('model')
    if not isinstance(model, str) or not 0 < len(model) <= 256:
        return None
    usage = message.get('usage')
    if usage is None and previous and message_id and (session, model, message_id) == previous[:3]:
        return previous
    if not isinstance(usage, dict):
        return None
    # Official statusline formula uses current input, including both cache
    # components. Output tokens are not part of that reading.
    values = (usage.get('input_tokens'), usage.get('cache_creation_input_tokens', 0),
              usage.get('cache_read_input_tokens', 0))
    if not all(_count(value) for value in values):
        return None
    return ContextSample(session, model, message_id, sum(values), None)
