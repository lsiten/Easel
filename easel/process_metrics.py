"""Python process/runtime observations; unavailable OS observations are omitted."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import gc
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time


@dataclass(frozen=True, slots=True)
class ProcessSnapshot:
    resident_bytes: int | None = None
    virtual_bytes: int | None = None
    start_seconds: float | None = None


def linux_snapshot(stat: str, memory: str, boot: str) -> ProcessSnapshot:
    # The comm field can contain spaces and parentheses; remaining fields start at state (field 3).
    fields = stat[stat.rindex(')') + 2:].split()
    pages = memory.split()
    boot_seconds = next(int(line.split()[1]) for line in boot.splitlines() if line.startswith('btime '))
    return ProcessSnapshot(int(pages[1]) * os.sysconf('SC_PAGE_SIZE'),
                           int(pages[0]) * os.sysconf('SC_PAGE_SIZE'),
                           boot_seconds + int(fields[19]) / os.sysconf('SC_CLK_TCK'))


def ps_snapshot(output: str) -> ProcessSnapshot:
    fields = output.split(maxsplit=2)
    return ProcessSnapshot(int(fields[0]) * 1024, int(fields[1]) * 1024,
                           datetime.strptime(fields[2].strip(), '%a %b %d %H:%M:%S %Y').timestamp())


def process_snapshot() -> ProcessSnapshot:
    if sys.platform.startswith('linux'):
        try:
            return linux_snapshot(Path('/proc/self/stat').read_text(), Path('/proc/self/statm').read_text(),
                                  Path('/proc/stat').read_text())
        except (OSError, ValueError, IndexError, StopIteration):
            return posix_snapshot()
    return posix_snapshot()


def posix_snapshot() -> ProcessSnapshot:
    if os.name == 'posix':
        try:
            result = subprocess.run(['ps', '-p', str(os.getpid()), '-o', 'rss=', '-o', 'vsz=', '-o', 'lstart='],
                                    capture_output=True, text=True, timeout=1, check=True,
                                    env={**os.environ, 'LC_ALL': 'C'})
            return ps_snapshot(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            return ProcessSnapshot()
    return ProcessSnapshot()


def peak_resident_bytes() -> int | None:
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, ValueError):
        return None
    if sys.platform == 'darwin':
        return int(peak)
    if sys.platform.startswith('linux'):
        return int(peak * 1024)
    return None


def render_process_metrics() -> list[str]:
    cpu = os.times()
    lines = ['# TYPE process_cpu_seconds_total counter', f'process_cpu_seconds_total {time.process_time()}',
             '# TYPE process_cpu_user_seconds_total counter', f'process_cpu_user_seconds_total {cpu.user}',
             '# TYPE process_cpu_system_seconds_total counter', f'process_cpu_system_seconds_total {cpu.system}']
    snapshot = process_snapshot()
    for name, value in (('process_resident_memory_bytes', snapshot.resident_bytes),
                        ('process_virtual_memory_bytes', snapshot.virtual_bytes),
                        ('process_peak_resident_memory_bytes', peak_resident_bytes()),
                        ('process_start_time_seconds', snapshot.start_seconds)):
        if value is not None:
            lines.extend((f'# TYPE {name} gauge', f'{name} {value}'))
    if snapshot.start_seconds is not None:
        lines.extend(('# TYPE process_uptime_seconds gauge', f'process_uptime_seconds {max(0, time.time() - snapshot.start_seconds)}'))
    lines.extend(('# TYPE python_info gauge',
                  f'python_info{{implementation={json.dumps(platform.python_implementation())},version={json.dumps(platform.python_version())}}} 1',
                  '# TYPE python_gc_enabled gauge', f'python_gc_enabled {int(gc.isenabled())}',
                  '# TYPE python_gc_allocations_since_collection gauge'))
    for generation, count in enumerate(gc.get_count()):
        lines.append(f'python_gc_allocations_since_collection{{generation="{generation}"}} {count}')
    for field in ('collections', 'collected', 'uncollectable'):
        name = f'python_gc_{field}_total'
        lines.append(f'# TYPE {name} counter')
        for generation, stats in enumerate(gc.get_stats()):
            lines.append(f'{name}{{generation="{generation}"}} {stats[field]}')
    return lines
