"""Read structured evidence emitted by the browser publishing scripts."""
import json

from easel.publish_models import PublishRun
from easel.publish_readback import (ReadbackOutcome, ReadbackQuery, ReadbackResult,
                                   normalize_verification_receipt, verify_readback)


def runner_readback(run: PublishRun, stdout: str) -> ReadbackResult:
    for line in reversed(stdout.splitlines()):
        if not line.startswith('{') or len(line) > 1024 * 1024:
            continue
        try:
            document = json.loads(line)
        except ValueError:
            continue
        if not isinstance(document, dict):
            continue
        receipt = document.get('easel_publish_receipt')
        if not isinstance(receipt, dict):
            continue
        observation = normalize_verification_receipt(run.platform, receipt)
        notes = observation['notes']
        work_id = notes[0]['id'] if notes else ''
        return verify_readback(run, observation, ReadbackQuery(platform_work_id=work_id))
    return ReadbackResult(ReadbackOutcome.UNVERIFIED, '', reason='structured_readback_unavailable')
