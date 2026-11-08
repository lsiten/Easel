from pathlib import Path

import pytest

from scripts import validate_skill_commands


WRAPPER = '''import argparse

parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest="cmd")
check = sub.add_parser("check")
check.add_argument("--json", action="store_true")
frame = sub.add_parser("frame")
frame.add_argument("args", nargs=argparse.REMAINDER)
for name in ("get", "render"):
    command = sub.add_parser(name)
    command.add_argument("args", nargs=argparse.REMAINDER)
args, rest = parser.parse_known_args()
'''


@pytest.mark.parametrize("case", [
    ("get music-search --limit 5", 0),
    ("render --allow-music-reuse series", 0),
    ("frame --full", 0),
    ("check --json", 0),
    ("check --limit 5", 1),
])
def test_command_audit_distinguishes_forwarded_flags_from_wrapper_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: tuple[str, int],
) -> None:
    arguments, status = case
    script = tmp_path / "skills/shared/scripts/wrapper.py"
    script.parent.mkdir(parents=True)
    script.write_text(WRAPPER, encoding="utf-8")
    document = tmp_path / "README.md"
    document.write_text(
        f"```bash\npython skills/shared/scripts/wrapper.py {arguments}\n```\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(validate_skill_commands, "ROOT", tmp_path)

    assert validate_skill_commands.main() == status


def test_command_audit_ignores_unowned_dependency_documentation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dependency = tmp_path / "skills/builtin/example/node_modules/library/README.md"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("```bash\npython missing.py --bad\n```\n", encoding="utf-8")
    monkeypatch.setattr(validate_skill_commands, "ROOT", tmp_path)

    assert validate_skill_commands.main() == 0
