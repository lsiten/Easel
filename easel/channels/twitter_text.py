"""Twitter v3 Unicode weights, transformed URLs, and joined emoji counting."""
from __future__ import annotations

import re
import unicodedata

_URL = re.compile(r'https?://[^\s<>]+|(?<![@\w])(?:[\w-]+\.)+[A-Za-z]{2,63}(?::\d+)?(?:/[^\s<>]*)?', re.IGNORECASE)


def _emoji(code: int) -> bool:
    return (0x1F000 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF or
            code in {0xA9, 0xAE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299})


def _text_weight(text: str) -> int:
    weight, index = 0, 0
    while index < len(text):
        code = ord(text[index])
        if _emoji(code):
            index += 1
            if 0x1F1E6 <= code <= 0x1F1FF and index < len(text) and 0x1F1E6 <= ord(text[index]) <= 0x1F1FF:
                index += 1
            while index < len(text):
                following = ord(text[index])
                if following in {0xFE0E, 0xFE0F} or 0x1F3FB <= following <= 0x1F3FF or 0xE0020 <= following <= 0xE007F:
                    index += 1
                elif following == 0x200D and index + 1 < len(text) and _emoji(ord(text[index + 1])):
                    index += 2
                else:
                    break
            weight += 2
            continue
        if text[index] in '#*0123456789':
            suffix = text[index + 1:index + 3]
            if suffix.startswith('\u20e3') or suffix == '\ufe0f\u20e3':
                index += 2 if suffix.startswith('\u20e3') else 3
                weight += 2
                continue
        weight += 1 if code <= 4351 or 8192 <= code <= 8205 or 8208 <= code <= 8223 or 8242 <= code <= 8247 else 2
        index += 1
    return weight


def weighted_length(text: str) -> int:
    normalized = unicodedata.normalize('NFC', text)
    total, first = 0, 0
    for match in _URL.finditer(normalized):
        url = match.group().rstrip('.,!?;:')
        while url.endswith(')') and url.count(')') > url.count('('): url = url[:-1]
        end = match.start() + len(url)
        total += _text_weight(normalized[first:match.start()]) + 23
        first = end
    return total + _text_weight(normalized[first:])
