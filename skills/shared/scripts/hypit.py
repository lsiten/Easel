"""Opt-in adapter for the external Hypit video runtime.

The adapter intentionally does not install, vendor, or discover Hypit from the
current working directory. Callers must provide a project and asset/output
roots explicitly. The default Easel engine remains OpenFilm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

PINNED_PACKAGE = "@hypit/hypit"
PINNED_VERSION = "0.3.2"
NODE_MIN = (22, 15, 0)
PNPM_REQUIRED = re.compile(r"^10\.33\.\d+$")
NODE_VERSION = "24.14.1"
LICENSE = {"name": "@hypit/hypit", "version": PINNED_VERSION, "license": "Apache-2.0", "source": "external-package"}


class HypitError(RuntimeError):
    code = "hypit-error"


class HypitConfigError(HypitError):
    code = "invalid-config"


class HypitVersionError(HypitError):
    code = "version-mismatch"


class HypitMissingExecutableError(HypitError):
    code = "missing-executable"


class HypitTimeoutError(HypitError):
    code = "timeout"


class HypitProcessError(HypitError):
    code = "non-zero-exit"


@dataclass(frozen=True)
class HypitConfig:
    project: Path
    asset_root: Path
    output_root: Path
    report_root: Path | None = None
    executable: Path | None = None
    timeout: int = 900

    def __post_init__(self) -> None:
        for name, value in (("project", self.project), ("asset_root", self.asset_root), ("output_root", self.output_root)):
            if not isinstance(value, Path) or not value.is_absolute():
                raise HypitConfigError(f"{name} must be an absolute path")
        if self.report_root is not None and (not isinstance(self.report_root, Path) or not self.report_root.is_absolute()):
            raise HypitConfigError("report_root must be an absolute path")
        if self.timeout <= 0:
            raise HypitConfigError("timeout must be positive")
        if self.executable is not None and not self.executable.is_absolute():
            raise HypitConfigError("executable must be an absolute path")

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "HypitConfig":
        missing = [name for name in ("project", "asset_root", "output_root") if not getattr(args, name, None)]
        if missing:
            raise HypitConfigError("explicit roots required: " + ", ".join(missing))
        return cls(Path(args.project).expanduser().resolve(), Path(args.asset_root).expanduser().resolve(),
                   Path(args.output_root).expanduser().resolve(),
                   Path(args.report_root).expanduser().resolve() if getattr(args, "report_root", None) else None,
                   Path(args.executable).expanduser().resolve() if getattr(args, "executable", None) else None,
                   int(getattr(args, "timeout", 900)))


def _version(value: str) -> tuple[int, ...]:
    match = re.search(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", value.strip())
    if not match:
        return ()
    return tuple(int(part or 0) for part in match.groups())


def _run(cmd: list[str], *, cwd: Path | None = None, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                              timeout=timeout, env=dict(os.environ))
    except FileNotFoundError as exc:
        raise HypitMissingExecutableError(f"executable not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HypitTimeoutError(f"command timed out after {timeout}s: {cmd[0]}") from exc
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HypitConfigError(f"invalid JSON config: {path}") from exc
    if not isinstance(value, dict):
        raise HypitConfigError(f"config must be an object: {path}")
    return value


def _package_version(project: Path) -> str | None:
    package = project / "package.json"
    if not package.is_file():
        return None
    data = _read_json(package)
    for section in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        value = data.get(section, {})
        if isinstance(value, dict) and PINNED_PACKAGE in value:
            return str(value[PINNED_PACKAGE])
    return None


def _integrity(project: Path) -> str | None:
    candidates = [project / "package-lock.json", project / "npm-shrinkwrap.json", project / "pnpm-lock.yaml"]
    for path in candidates:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="ignore")
        # npm lock integrity is authoritative. pnpm lock entries also commonly
        # carry an integrity field. Record a digest even when metadata is absent.
        marker = re.search(r"(?:@hypit/hypit|hypit)[:\s\S]{0,500}?integrity:\s*([^\s,}\"]+)", text)
        if marker:
            return marker.group(1)
        marker = re.search(r'"integrity"\s*:\s*"([^"]+)"', text)
        if marker and ("hypit" in text.lower()):
            return marker.group(1)
        return "sha256:" + hashlib.sha256(raw).hexdigest()
    return None


def _which(name: str) -> str | None:
    return shutil.which(name)


def _probe(path: str | None, args: list[str], *, timeout: int = 30) -> tuple[bool, str]:
    if not path:
        return False, "missing executable"
    try:
        rc, out = _run([path, *args], timeout=timeout)
    except HypitError as exc:
        return False, exc.code
    return rc == 0, out.splitlines()[0] if out else ("ok" if rc == 0 else f"rc={rc}")


def resolve_executable(config: HypitConfig) -> Path:
    if config.executable is not None:
        return config.executable
    local = config.project / "node_modules" / ".bin" / "hypit"
    if local.is_file():
        return local
    raise HypitMissingExecutableError("Hypit executable is missing; pass --executable or install it in the explicit project")


def _redact(value: Any) -> Any:
    secret = re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization|cookie)")
    assignment = re.compile(
        r"(?i)(\b(?:token|secret|password|api[_-]?key|authorization|cookie)\b\s*[:=]\s*)"
        r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    )

    def redact_text(text: str) -> str:
        def replace_assignment(match: re.Match[str]) -> str:
            raw = match.group(2)
            if raw[:1] in {"\"", "'"} and raw[-1:] == raw[:1]:
                return match.group(1) + raw[:1] + "[REDACTED]" + raw[:1]
            return match.group(1) + "[REDACTED]"

        text = assignment.sub(replace_assignment, text)
        return re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", text)

    if isinstance(value, Mapping):
        return {str(k): ("[REDACTED]" if secret.search(str(k)) else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def readiness(config: HypitConfig) -> dict[str, Any]:
    if not config.project.is_dir():
        raise HypitConfigError(f"project does not exist: {config.project}")
    if not config.asset_root.is_dir():
        raise HypitConfigError(f"asset root does not exist: {config.asset_root}")
    config.output_root.mkdir(parents=True, exist_ok=True)
    if config.report_root is not None:
        config.report_root.mkdir(parents=True, exist_ok=True)
    node = _which("node")
    pnpm = _which("pnpm")
    node_ok, node_detail = _probe(node, ["--version"])
    pnpm_ok, pnpm_detail = _probe(pnpm, ["--version"])
    node_actual = _version(node_detail)
    node_ok = node_ok and node_actual >= NODE_MIN and (config.project / ".node-version").read_text(encoding="utf-8").strip() == NODE_VERSION if (config.project / ".node-version").is_file() else False
    pnpm_actual = pnpm_detail.splitlines()[0].strip().lstrip("v")
    pnpm_ok = pnpm_ok and bool(PNPM_REQUIRED.fullmatch(pnpm_actual))
    ffmpeg_ok, ffmpeg_detail = _probe(_which("ffmpeg"), ["-version"])
    chrome_path = _which("google-chrome") or _which("chromium") or _which("chromium-browser")
    chrome_ok, chrome_detail = _probe(chrome_path, ["--version"])
    package_version = _package_version(config.project)
    pin_ok = package_version == PINNED_VERSION
    executable = None
    executable_ok = False
    try:
        executable = str(resolve_executable(config))
        executable_ok = Path(executable).is_file() and os.access(executable, os.X_OK)
    except HypitMissingExecutableError:
        pass
    result = {
        "ok": bool(node_ok and pnpm_ok and ffmpeg_ok and chrome_ok and pin_ok and executable_ok),
        "engine": "hypit",
        "version": PINNED_VERSION,
        "requirements": {"node": {"ok": node_ok, "required": ">=22.15.0", "configured": NODE_VERSION, "detail": node_detail},
                         "pnpm": {"ok": pnpm_ok, "required": "10.33.x", "detail": pnpm_detail},
                         "ffmpeg": {"ok": ffmpeg_ok, "detail": ffmpeg_detail},
                         "chrome": {"ok": chrome_ok, "detail": chrome_detail}},
        "package": {"name": PINNED_PACKAGE, "version": package_version, "integrity": _integrity(config.project), "pinned": pin_ok},
        "executable": {"ok": executable_ok, "path": executable},
        "license": LICENSE,
        "runtime_metadata": {"engine": "hypit", "package": PINNED_PACKAGE, "version": PINNED_VERSION,
                            "integrity": _integrity(config.project), "project": str(config.project),
                            "asset_root": str(config.asset_root), "output_root": str(config.output_root)},
    }
    return _redact(result)


def translate_status(status: str, *, returncode: int | None = None, timed_out: bool = False) -> str:
    value = str(status).lower()
    if timed_out or value in {"timeout", "timed_out"}:
        return "failed"
    if value in {"cancelled", "canceled", "aborted"}:
        return "cancelled"
    if value in {"paused", "running", "succeeded", "failed", "planned"}:
        return value
    if returncode is not None and returncode != 0:
        return "failed"
    return "running"


def run(config: HypitConfig, args: list[str] | None = None) -> dict[str, Any]:
    if _package_version(config.project) != PINNED_VERSION:
        raise HypitVersionError(f"{PINNED_PACKAGE} must be pinned to {PINNED_VERSION}")
    executable = resolve_executable(config)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise HypitMissingExecutableError(f"Hypit executable is not executable: {executable}")
    command = [str(executable), *(args or []), "--asset-root", str(config.asset_root), "--output-root", str(config.output_root)]
    try:
        proc = subprocess.run(command, cwd=str(config.project), capture_output=True, text=True, timeout=config.timeout,
                              env={**os.environ, "HYPIT_PROJECT": str(config.project)})
    except subprocess.TimeoutExpired as exc:
        raise HypitTimeoutError(f"Hypit timed out after {config.timeout}s") from exc
    except FileNotFoundError as exc:
        raise HypitMissingExecutableError(str(executable)) from exc
    if proc.returncode != 0:
        raise HypitProcessError(f"Hypit exited with status {proc.returncode}: {_redact(proc.stderr[-500:])}")
    return _redact({"engine": "hypit", "version": PINNED_VERSION, "status": "succeeded", "returncode": 0,
                    "stdout": proc.stdout, "output_root": str(config.output_root), "license": LICENSE,
                    "runtime_metadata": {"engine": "hypit", "package": PINNED_PACKAGE, "version": PINNED_VERSION,
                                        "integrity": _integrity(config.project)}})


def manifest_for_status(base: Mapping[str, Any], status: str, *, returncode: int | None = None, timed_out: bool = False) -> Any:
    """Translate Hypit provider status into a validated VideoRunManifest."""
    from video_run_manifest import VideoRunManifest
    state = translate_status(status, returncode=returncode, timed_out=timed_out)
    values = dict(base)
    values["engine"] = "hypit"
    values["state"] = state
    values.setdefault("provenance", {})
    values["provenance"] = {**values["provenance"], "engine": "hypit", "package": PINNED_PACKAGE, "version": PINNED_VERSION, "license": LICENSE}
    return VideoRunManifest.create(**values)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Opt-in Hypit readiness adapter")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("readiness", "run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--project", required=True)
        cmd.add_argument("--asset-root", required=True)
        cmd.add_argument("--output-root", required=True)
        cmd.add_argument("--report-root")
        cmd.add_argument("--executable")
        cmd.add_argument("--timeout", type=int, default=900)
        if name == "run":
            cmd.add_argument("args", nargs=argparse.REMAINDER)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        config = HypitConfig.from_args(args)
        result = readiness(config) if args.command == "readiness" else run(config, args.args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ok", True) else 1
    except HypitError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stdout)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
