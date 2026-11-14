"""Re-read the live host policy immediately before a platform commit."""
import os
from pathlib import Path

from easel.direct_api import read_settings
from easel.publish_policy import publishing_allowed
from browser_identity import IdentityPage, authenticated_uid


class PublishGuardError(RuntimeError):
    """A live publication boundary lost its reviewed authorization."""


def require_publish_allowed(platform: str) -> None:
    root = Path(__file__).resolve().parents[3]
    path = Path(os.environ.get('EASEL_PUBLISH_POLICY_FILE', str(root / '.env')))
    if not publishing_allowed(platform, read_settings(path)):
        raise PublishGuardError('当前发布策略已暂停此平台，请重新审核任务')


def require_publish_actor(platform: str, page: IdentityPage) -> None:
    expected = os.environ.get('EASEL_PUBLISH_ACCOUNT_UID', '').strip()
    if not expected:
        return
    if os.environ.get('EASEL_PUBLISH_PLATFORM', '') != platform:
        raise PublishGuardError('发布平台与已审核任务不一致')
    try:
        actual = authenticated_uid(platform, page, for_publish=True)
    except Exception as error:  # noqa: BROAD_EXCEPT_OK — identity transport failures must stop every commit
        raise PublishGuardError('当前认证账号无法核验，请重新登录并审核') from error
    if not actual or actual != expected:
        raise PublishGuardError('当前认证账号与已审核账号不一致，请重新审核任务')


def require_publish_commit(platform: str, page: IdentityPage) -> None:
    require_publish_allowed(platform)
    require_publish_actor(platform, page)
    require_publish_allowed(platform)
