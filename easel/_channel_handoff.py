"""Expose bounded official app handoff capabilities from server-owned receipts."""
from dataclasses import dataclass
import math
from urllib.parse import urlsplit

from easel.channels.models import PublishReceipt
from easel.channels import ChannelAdapter, Credential
from easel._channel_publish_models import ChannelReconcileRequest
from easel._channel_publish_readback import official_readback
from easel.publish_models import PublishRun
from easel.publish_readback import ReadbackOutcome, canonical_work_url


@dataclass(frozen=True, slots=True)
class HandoffInfo:
    required: bool = False
    action_url: str = ''
    expires_at: float = 0


def handoff_info(platform: str, receipt: PublishReceipt | None) -> HandoffInfo:
    if receipt is None or receipt.state != 'requires_user_action':
        return HandoffInfo()
    candidate = receipt.details.get('schema') or receipt.details.get('handoff_url') or ''
    url = ''
    if isinstance(candidate, str) and len(candidate) <= 65536:
        try:
            parsed = urlsplit(candidate)
            port = parsed.port
        except ValueError:
            return HandoffInfo(True)
        if platform == 'douyin-open' and parsed.scheme == 'snssdk1128' and parsed.netloc == 'openplatform' and parsed.path == '/share':
            url = candidate
        hosts = {'douyin-open': {'open.douyin.com'}, 'wechat-channels-open': {'channels.weixin.qq.com'},
                 'rednote-link': {'www.xiaohongshu.com', 'creator.xiaohongshu.com'}}.get(platform, set())
        if parsed.scheme == 'https' and parsed.hostname in hosts and port in {None, 443} and not parsed.username and not parsed.password:
            url = candidate
    expiry = receipt.details.get('expires_at')
    expires_at = float(expiry) if type(expiry) in (int, float) and math.isfinite(expiry) else 0
    return HandoffInfo(True, url, expires_at)


def verify_handoff(adapter: ChannelAdapter, credential: Credential, run: PublishRun, receipt: PublishReceipt) -> PublishReceipt:
    """Require content and time corroboration for a client-selected work link."""
    if receipt.state != 'published' or adapter.definition.platform not in {'wechat-channels-open', 'rednote-link'} or not receipt.details.get('candidate_work_id'):
        return receipt
    result = official_readback(adapter, credential, run, ChannelReconcileRequest(persona=run.persona,
        account_uid=run.account_id, platform_work_id=receipt.platform_work_id))
    match result.outcome:
        case ReadbackOutcome.VERIFIED:
            return receipt.model_copy(update={'permalink': result.permalink or canonical_work_url(run.platform, receipt.permalink)})
        case ReadbackOutcome.UNVERIFIED | ReadbackOutcome.AMBIGUOUS | ReadbackOutcome.ERROR:
            return receipt.model_copy(update={'state': 'requires_user_action', 'platform_work_id': '', 'permalink': '',
                'details': {**receipt.details, 'provider_verified': False, 'verification_reason': result.reason}})
        case unreachable:
            from typing_extensions import assert_never
            assert_never(unreachable)
