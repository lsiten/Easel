"""Provider pagination validation without following remote next-page URLs."""
from easel.channels.models import ChannelError


def validate_pagination(platform: str, cursor: str, limit: int, direction: str) -> int:
    if len(cursor) > 2048 or type(limit) is not int or not 1 <= limit <= 100 or direction not in {'next', 'previous'}:
        raise ChannelError(platform, 'pagination_invalid')
    if direction == 'previous' and platform not in {'facebook', 'instagram', 'threads'}:
        raise ChannelError(platform, 'previous_pagination_unsupported', status=405)
    maximum = {'youtube': 50, 'tiktok': 20}.get(platform, 100)
    return min(limit, maximum)
