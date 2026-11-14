"""Topic parsing compatible with the native provider's UTF-16 offsets."""
import re

_HAN = (
    '\u2e80-\u2e99\u2e9b-\u2ef3\u2f00-\u2fd5\u3005\u3007\u3021-\u3029\u3038-\u303b'
    '\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufa6d\ufa70-\ufad9'
    '\U00016fe2-\U00016fe3\U00016ff0-\U00016ff6\U00020000-\U0002a6df'
    '\U0002a700-\U0002b81d\U0002b820-\U0002cead\U0002ceb0-\U0002ebe0'
    '\U0002ebf0-\U0002ee5d\U0002f800-\U0002fa1d\U00030000-\U0003134a\U00031350-\U00033479'
)
_TOPIC = re.compile(r'#([A-Za-z0-9_' + _HAN + r']+)')


def strip_topics(body: str) -> str:
    value = _TOPIC.sub('', body)
    value = re.sub(r'[ \t]+', ' ', value)
    value = re.sub(r' *\n *', '\n', value)
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def parse_topics(body: str) -> list[str]:
    return list(dict.fromkeys(match.group(1) for match in _TOPIC.finditer(body)))


def topic_insertions(body: str) -> list[dict[str, str | int]]:
    result = []
    seen = set()
    for match in _TOPIC.finditer(body):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        offset = len(strip_topics(body[:match.start()]).encode('utf-16-le')) // 2
        result.append({'name': name, 'start': offset})
    return result
