"""ASGI-level request metrics including streamed response completion/cancellation."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sqlite3
import time
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from easel.queue_metrics import render_queues, render_draft_pools
from easel.process_metrics import render_process_metrics

BOUNDS = (0.005, 0.025, 0.1, 0.5, 1, 5, 30, 120, 900)


class HttpMetrics:
    """Mutable process-local accumulator; labels are bounded routes, methods and statuses."""
    def __init__(self) -> None:
        self.inflight: Counter[str] = Counter()
        self.counts: Counter[tuple[str, str, int, str]] = Counter()
        self.durations: defaultdict[tuple[str, str, int, str], float] = defaultdict(float)
        self.buckets: defaultdict[tuple[str, str, int, str], Counter[float]] = defaultdict(Counter)

    def observe(self, labels: tuple[str, str, int, str], duration: float) -> None:
        self.counts[labels] += 1
        self.durations[labels] += duration
        for bound in BOUNDS:
            self.buckets[labels][bound] += int(duration <= bound)

    def render(self, outputs: Path) -> str:
        lines = ['# TYPE easel_http_inflight gauge', '# TYPE easel_http_requests_total counter',
                 '# TYPE easel_http_duration_seconds histogram']
        for method, count in sorted(self.inflight.items()):
            lines.append(f'easel_http_inflight{{method="{method}"}} {count}')
        for key, count in sorted(self.counts.items()):
            method, route, status, outcome = key
            labels = f'method="{method}",route={json.dumps(route)},status="{status}",outcome="{outcome}"'
            lines.extend((f'easel_http_requests_total{{{labels}}} {count}',
                          f'easel_http_duration_seconds_sum{{{labels}}} {self.durations[key]}',
                          f'easel_http_duration_seconds_count{{{labels}}} {count}'))
            for bound in BOUNDS:
                lines.append(f'easel_http_duration_seconds_bucket{{{labels},le="{bound}"}} {self.buckets[key][bound]}')
            lines.append(f'easel_http_duration_seconds_bucket{{{labels},le="+Inf"}} {count}')
        lines.extend(('# TYPE easel_queue_jobs gauge', '# TYPE easel_queue_started_total counter',
                      '# TYPE easel_queue_completed_total counter', '# TYPE easel_queue_failed_total counter',
                      '# TYPE easel_queue_attempts_total counter', '# TYPE easel_queue_processing_seconds histogram',
                      '# TYPE easel_queue_waiting_seconds histogram'))
        from easel.generation_app_config import effective_config
        queue = effective_config(outputs).queue
        lines.extend(('# TYPE easel_queue_pool_jobs gauge', '# TYPE easel_queue_concurrency_limit gauge'))
        for name, pool, limit in (('image', 'normal', queue.imageConcurrency), ('video', 'normal', queue.videoConcurrency),
                                  ('draft', 'normal', queue.draftConcurrency), ('draft', 'low', queue.lowPriorityConcurrency)):
            lines.append(f'easel_queue_concurrency_limit{{queue="{name}",pool="{pool}"}} {limit}')
        for path in (outputs / '_generation/jobs.sqlite3', outputs / '_publish/ledger.sqlite3', outputs / '_publish/tasks.sqlite3'):
            if path.is_file() and not path.is_symlink():
                with sqlite3.connect(f'{path.as_uri()}?mode=ro', uri=True) as db:
                    lines.extend(render_queues(db))
                    lines.extend(render_draft_pools(db, queue.lowPriorityMinPriority))
        lines.extend(render_process_metrics())
        return '\n'.join(lines) + '\n'


class HttpMetricsMiddleware:
    def __init__(self, app: ASGIApp, metrics: HttpMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http' or scope.get('path') in {'/metrics', '/health'}:
            await self.app(scope, receive, send)
            return
        method = scope['method'] if scope['method'] in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'} else 'OTHER'
        status = 500
        complete = False
        started = time.monotonic()
        self.metrics.inflight[method] += 1

        async def observe_send(message: Message) -> None:
            nonlocal status, complete
            await send(message)
            if message['type'] == 'http.response.start':
                status = message['status']
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                complete = True

        try:
            await self.app(scope, receive, observe_send)
        finally:
            self.metrics.inflight[method] -= 1
            route = scope.get('route')
            template = getattr(route, 'path', '__unmatched__')
            outcome = 'completed' if complete else 'error' if status == 500 else 'cancelled'
            effective_status = status if complete or status == 500 else 499
            self.metrics.observe((method, template, effective_status, outcome), time.monotonic() - started)
