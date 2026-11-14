"""Resolve the active Easel application without importing a second entrypoint copy."""
from __future__ import annotations

import sys
from types import ModuleType


def _active() -> ModuleType:
    packaged = sys.modules.get("web.app")
    if packaged is not None and hasattr(packaged, "app"):
        return packaged
    legacy = sys.modules.get("app")
    if legacy is not None and hasattr(legacy, "app"):
        return legacy
    raise RuntimeError("Easel application module is not initialized")


class _ServerBinding:
    def __getattr__(self, name: str):
        return getattr(_active(), name)


server = _ServerBinding()
