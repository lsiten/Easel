"""issue #26 剩余项的回归测试：npm registry 设置失败不该静默杀掉整个安装。

覆盖 setup.sh step 2 的 npm registry 选择：
  ① 写 registry / 连通性探测失败时，安装必须继续并给出 warn，而不是无提示退出；
  ② 成功时仍如实报 ok。

按内容锚点切出 setup.sh 的真实片段，在沙箱里用假 npm 跑。
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SH = PROJECT_ROOT / "setup.sh"


def _bash_works() -> bool:
    try:
        p = subprocess.run(["bash", "-c", "echo ok"], capture_output=True, text=True,
                           timeout=30, errors="replace")
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0 and p.stdout.strip() == "ok"


needs_bash = pytest.mark.skipif(not _bash_works(), reason="没有可用的 bash")


def _slice(lines: list[str], start: str, end: str, *, keep_end: bool) -> str:
    i = next(n for n, line in enumerate(lines) if line.startswith(start))
    j = next(n for n, line in enumerate(lines) if n > i and line.startswith(end))
    return "\n".join(lines[i: j + 1 if keep_end else j])


# ── ① step 2：npm registry 失败不再静默杀掉安装 ──────────────────

@needs_bash
def test_npm_registry_failure_does_not_abort_setup(tmp_path: Path) -> None:
    """npm 连通性探测失败时，脚本必须继续（带 warn），而不是无提示退出。"""
    lines = SETUP_SH.read_text(encoding="utf-8").splitlines()
    block = _slice(lines, "# ---- 2. npm 源 ----", "# ---- 3. 安装 easel CLI", keep_end=False)

    # 假的 npm：ping 一律失败（模拟 registry 跨会话不可达）。
    fake_npm = tmp_path / "npm"
    fake_npm.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
    fake_npm.chmod(0o755)

    script = textwrap.dedent(f"""
        set -euo pipefail
        info()  {{ echo "[easel] $*"; }}
        ok()    {{ echo "OK|$*"; }}
        warn()  {{ echo "WARN|$*"; }}
        step()  {{ echo "STEP|$*"; }}
        npm()   {{ "{fake_npm}" "$@"; }}
    """) + "\n" + block + "\necho REACHED_END\n"

    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=60, env={"PATH": os.environ["PATH"]})
    assert proc.returncode == 0, f"npm 失败后脚本应继续，实际退出码 {proc.returncode}：{proc.stderr}"
    assert "REACHED_END" in proc.stdout, f"脚本提前终止：{proc.stdout}"
    assert "WARN|" in proc.stdout, "失败时应给出 warn，而不是静默"
    assert "OK|npm registry" not in proc.stdout, "npm 实际失败却报了成功"


@needs_bash
def test_npm_registry_success_still_reports_ok(tmp_path: Path) -> None:
    lines = SETUP_SH.read_text(encoding="utf-8").splitlines()
    block = _slice(lines, "# ---- 2. npm 源 ----", "# ---- 3. 安装 easel CLI", keep_end=False)
    script = textwrap.dedent("""
        set -euo pipefail
        info()  { echo "[easel] $*"; }
        ok()    { echo "OK|$*"; }
        warn()  { echo "WARN|$*"; }
        step()  { echo "STEP|$*"; }
        npm()   { return 0; }
    """) + "\n" + block + "\necho REACHED_END\n"
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                          timeout=60, env={"PATH": os.environ["PATH"]})
    assert proc.returncode == 0
    assert "OK|npm registry: npmjs.org" in proc.stdout
