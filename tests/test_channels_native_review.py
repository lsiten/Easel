"""Independent review probes run the actual native handoff and completion monitor."""
import asyncio
import time

import pytest

from easel.channel_monitor import monitor_one
from easel.channels import ChannelError
from easel.native_handoff_models import HandoffStart
from easel.publish_models import RunState
from tests.test_native_handoff import harness, intent


def test_submitted_native_job_is_verified_after_action_expiry(harness, monkeypatch):
    from easel import channel_monitor
    service, calls, completed = harness
    template = service.create('douyin-open', intent())
    action = service.start(template['template_id'], template['token'], HandoffStart(request_key='visitor', reviewed=True))
    assert service.ledger.get_run(action['run_id']).state == RunState.SUBMITTED
    completed.append(True)
    future = time.time() + 7200
    monkeypatch.setattr(channel_monitor.time, 'time', lambda: future)
    async def finalize(platform, run_id, scope):
        assert platform == 'douyin-open' and scope['persona'] == 'alice'
        return 200, service.poll_owned(scope['persona'], run_id)
    assert asyncio.run(monitor_one(service.ledger, service.root, finalize))
    done = service.ledger.get_run(action['run_id'])
    assert done.state == RunState.PUBLISHED and done.platform_work_id == 'actual-video'
    assert len([call for call in calls if call.url.path == '/share-id/' and not call.url.params.get('share_id')]) == 1
    with pytest.raises(ChannelError, match='template_expired'):
        service.public_view(template['template_id'], template['token'])


def test_public_native_grant_rejects_non_ascii_token_as_typed_error(harness):
    service, _, _ = harness
    template = service.create('douyin-open', intent())
    with pytest.raises(ChannelError, match='template_not_found'):
        service.public_view(template['template_id'], '无效令牌')
