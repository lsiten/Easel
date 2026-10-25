"""setup.ps1 的静态断言 —— 在 Linux 上守住 Windows 专属的几类缺陷。

本仓库作者的开发机是 Linux，改 PowerShell 没法本地跑真实安装。pwsh 7 虽然能装在
Linux 上做解析和 PSScriptAnalyzer，但**复现不了 Windows PowerShell 5.1 的关键行为**
（实测：把 ErrorActionPreference=Continue 去掉，在 pwsh 7 上测试照样通过）。而 5.1
才是 Windows 自带、绝大多数用户实际跑的版本。

所以凡是能表达成「源码里必须/不许出现某种写法」的，都在这里钉住，把 Windows 盲区
压到最小；剩下必须真跑的部分交给 CI 上 windows-latest 的双 shell 任务。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_PS1 = PROJECT_ROOT / "setup.ps1"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def test_dependency_install_preserves_process_path() -> None:
    text = _read(SETUP_PS1)
    start = text.index("function Install-RequiredCommand")
    body = text[start:text.index("function Add-Warning", start)]
    assert re.search(r"\$env:Path\s*=\s*\$env:Path\s*\+", body)
    assert "GetEnvironmentVariable('Path', 'Machine')" in body
    assert "GetEnvironmentVariable('Path', 'User')" in body


@pytest.mark.parametrize("shell_name", ["powershell", "pwsh"])
def test_dependency_install_path_in_powershell(shell_name: str, tmp_path: Path) -> None:
    executable = shutil.which(shell_name)
    if not executable:
        pytest.skip(f"{shell_name} is unavailable")
    text = _read(SETUP_PS1)
    start = text.index("function Assert-Command")
    functions = text[start:text.index("function Add-Warning", start)]
    script = tmp_path / "path-probe.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + functions
        + r"""
function Info($Message) {}
function Fail($Message) { throw $Message }
$script:Installed = $false
function Get-Command($Name, $ErrorAction) {
    if ($Name -eq 'winget' -or $script:Installed) {
        return [pscustomobject]@{ Name = $Name }
    }
}
function winget {
    $script:Installed = $true
    $global:LASTEXITCODE = 0
}
$originalPath = 'C:\easel probe\node;C:\easel probe\python'
$env:Path = $originalPath
Install-RequiredCommand 'easel-path-probe' 'Easel.Test' 'mock dependency'
if (-not $script:Installed) { throw 'Mock dependency was not installed' }
$expectedPath = $originalPath + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
if ($env:Path -ne $expectedPath) { throw 'Process PATH was lost or registry PATH was not refreshed' }
""",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(script)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_non_ascii_ps1_files_keep_bom() -> None:
    """含非 ASCII 字符的 .ps1 必须带 UTF-8 BOM。

    PS 5.1 没有 BOM 时按系统 ANSI 代码页解析，中文字符串会乱码甚至报语法错误。
    本仓库每个 .ps1 都带中文注释，所以这条对全部文件生效 —— 新增 ps_tolerance_probe.ps1
    时就漏了 BOM，而它恰恰要在 CI 的 5.1 下运行，是 PSScriptAnalyzer 替我抓到的。
    """
    import subprocess

    out = subprocess.run(["git", "ls-files", "*.ps1"], cwd=PROJECT_ROOT,
                         capture_output=True, text=True, timeout=60).stdout
    checked = 0
    for rel in out.split():
        if "vendor/" in rel:
            continue
        full = PROJECT_ROOT / rel
        if not full.is_file():
            continue  # 已从工作树删除但仍在 git 索引里的文件（如 scripts/gateway.ps1）
        data = full.read_bytes()
        try:
            data.decode("ascii")
            continue        # 纯 ASCII 文件不需要 BOM
        except UnicodeDecodeError:
            pass
        assert data[:3] == b"\xef\xbb\xbf", f"{rel} 含非 ASCII 字符，必须带 UTF-8 BOM"
        checked += 1
    assert checked >= 2, f"只检查到 {checked} 个文件，git ls-files 可能失效了"


def test_no_powershell7_only_syntax() -> None:
    """不许用 PS7-only 语法：Windows 自带的是 5.1，会直接语法错误。

    三元运算符 `? :`、null 合并 `??`、管道链 `&&`/`||` 都是 7.0 才有的。
    这一条是实打实踩过的：本次改动初稿里就用了三元运算符，pwsh 7 解析得过。
    """
    for p in (SETUP_PS1,):
        for n, line in enumerate(_read(p).splitlines(), 1):
            code = line.split("#")[0]
            assert "??" not in code, f"{p.name}:{n} 用了 PS7 的 ?? 运算符"
            assert not re.search(r"\)\s*\?\s|\s\?\s.*\s:\s", code), \
                f"{p.name}:{n} 疑似用了 PS7 的三元运算符：{line.strip()}"
            assert not re.search(r"(?<![|&])&&(?![&])|(?<![|&])\|\|(?![|])", code), \
                f"{p.name}:{n} 用了 PS7 的管道链运算符"
