"""setup.sh / setup.ps1 / install_tool 认证与安装路径的回归测试。

保留与当前安装路径相关的用例；曾经依赖已退役引擎配置写入的认证链路用例
（setup.sh 认证块、doctor 路由检查）已随该引擎退役删除。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SETUP_SH = PROJECT_ROOT / "setup.sh"
SETUP_PS1 = PROJECT_ROOT / "setup.ps1"

sys.path.insert(0, str(PROJECT_ROOT))


# ── setup.sh：别回到硬编码占位符比较 ────────────────────────────────────


def test_setup_sh_has_no_bare_placeholder_comparisons():
    """别再回到「各写各的 != 占位符」：认证判定统一走 usable_key。"""
    text = SETUP_SH.read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "sk-ant-REPLACE_ME" not in body, "认证判定里仍有硬编码占位符比较"


# ── setup.ps1：-and 必须处在表达式模式 ────────────────────────────────


def test_ps1_logical_and_not_in_command_position():
    """`if (Foo $x -and $y)` 里 -and 会被当成 Foo 的参数名静默吞掉，函数调用必须加括号。"""
    offenders = [
        (n, line) for n, line in enumerate(SETUP_PS1.read_text(encoding="utf-8").splitlines(), 1)
        if not line.lstrip().startswith("#")
        and re.search(r"\(\s*[A-Z]\w*-\w+[^()]*\s-(and|or)\s", line)
    ]
    assert not offenders, "这些行的 -and/-or 处在命令参数位，守卫会被静默丢弃：\n" + \
        "\n".join(f"  {n}: {line.strip()}" for n, line in offenders)


# ── install_tool：别展开、别降级用户 PATH ──────────────────────────────


def _install_tool_code() -> str:
    """只取代码行 —— 注释里会原样引用那两个被淘汰的 API 名来解释「为什么不用」。"""
    src = (PROJECT_ROOT / "skills" / "shared" / "scripts" / "install_tool.py").read_text(encoding="utf-8")
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))


def test_user_path_read_does_not_expand_env_refs():
    """读用户 PATH 必须拿原值：GetEnvironmentVariable 会把 %USERPROFILE% 展开成字面路径。"""
    code = _install_tool_code()
    assert "GetEnvironmentVariable('Path','User')" not in code, \
        "又用回了会展开 %VAR% 的 API"
    assert "DoNotExpandEnvironmentNames" in code, "没有按原值读取用户 PATH"


def test_user_path_write_preserves_value_kind():
    """写回必须沿用原 ValueKind，否则 REG_EXPAND_SZ 会被降级成 REG_SZ。"""
    code = _install_tool_code()
    assert "SetEnvironmentVariable('Path'" not in code, "又用回了固定写 REG_SZ 的 API"
    assert re.search(r"SetValue\('Path',.*\$kind\)", code), "写回时没有沿用原 ValueKind"


def test_user_path_dir_never_interpolated_into_script():
    """目录只能经环境变量递进去：PATH 里一个单引号就能让拼串写法变成代码执行。"""
    sys.path.insert(0, str(PROJECT_ROOT / "skills" / "shared" / "scripts"))
    import install_tool

    assert "$env:EASEL_NEW_DIR" in install_tool._PS_APPEND_USER_PATH
    # 脚本是模块级常量，不带任何格式化占位，不可能把目录拼进去
    assert "%s" not in install_tool._PS_APPEND_USER_PATH
    assert ".format(" not in install_tool._PS_APPEND_USER_PATH


def test_json_output_survives_non_utf8_locale():
    """配方表输出全是中文，stdout 是管道时不能被系统 locale 编码噎死。

    Windows 上 stdout 一旦被面板/agent 捕获，Python 就按 cp936/cp1252 写，中文直接
    UnicodeEncodeError、stdout 一个字节不出 —— 调用方只看到「配方表是空的」，
    安装接口的 id 白名单随之永远为空，装什么都被拒。这里用 PYTHONIOENCODING
    在 Linux 上复现同一条件。
    """
    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "skills" / "shared" / "scripts" / "install_tool.py"),
         "--json", "list"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"})
    assert proc.returncode == 0, f"非 UTF-8 locale 下崩了：{proc.stderr[-500:]}"
    ids = {t["id"] for t in json.loads(proc.stdout)["tools"]}
    assert "node" in ids, f"配方表没出来：{ids}"


@pytest.mark.skipif(os.name == "nt", reason="Windows 上这个调用会真改注册表，不能在测试里跑")
def test_user_path_is_noop_off_windows():
    """非 Windows 平台一律不碰系统配置。

    注意别把这条写成无条件跑：`_ensure_user_path` 在 Windows 上是**真的**去改当前
    用户的 PATH 注册表项的，测试里调一次就会把参数里那个假目录永久写进去。
    """
    sys.path.insert(0, str(PROJECT_ROOT / "skills" / "shared" / "scripts"))
    import install_tool

    assert install_tool._ensure_user_path("/tmp/whatever") == "skip"
