#!/usr/bin/env python3
"""Check that Python commands documented by Skills match real CLI help."""

from __future__ import annotations

import re
import shlex
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .validate_document_paths import documents
else:
    from validate_document_paths import documents


ROOT = Path(__file__).resolve().parents[1]
FENCE_RE = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.S)
OPTION_RE = re.compile(r"(?<!\S)(--[a-zA-Z][a-zA-Z0-9-]*|-[a-zA-Z])")


def commands(path: Path):
    text = path.read_text(encoding="utf-8")
    for block in FENCE_RE.findall(text):
        logical = re.sub(r"\\\s*\n\s*", " ", block)
        for raw in logical.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                tokens = shlex.split(line, comments=True)
            except ValueError:
                continue
            for index, token in enumerate(tokens[:-1]):
                if token in {"python", "python3"} and tokens[index + 1].endswith(".py"):
                    yield line, tokens[index + 1 :]
                    break


def resolve_script(doc: Path, token: str) -> Path | None:
    if any(char in token for char in "$<>{}*"):
        return None
    candidate = Path(token)
    if candidate.is_absolute():
        return candidate
    if token.startswith(("skills/", "agents/")):
        return ROOT / candidate
    if token.startswith("scripts/"):
        for parent in doc.parents:
            if parent == ROOT:
                break
            if (parent / "SKILL.md").is_file():
                return parent / candidate
        return ROOT / candidate
    return doc.parent / candidate


@dataclass(frozen=True, slots=True)
class CliSchema:
    options: frozenset[str] = frozenset()
    forwarding: frozenset[str] = frozenset()


def forwarding_subcommands(tree: ast.Module) -> frozenset[str]:
    """Identify subparsers that intentionally accept unknown downstream options."""
    nodes = list(ast.walk(tree))
    if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "parse_known_args" for node in nodes):
        return frozenset()
    loop_names: dict[str, set[str]] = {}
    for node in nodes:
        if (isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                and isinstance(node.iter, (ast.Tuple, ast.List))):
            loop_names[node.target.id] = {
                item.value for item in node.iter.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
    parsers: dict[str, set[str]] = {}
    for node in nodes:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Attribute)
                and node.value.func.attr == "add_parser" and node.value.args):
            continue
        argument = node.value.args[0]
        names: set[str] = set()
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            names.add(argument.value)
        elif isinstance(argument, ast.Name):
            names.update(loop_names.get(argument.id, ()))
        for target in node.targets:
            if isinstance(target, ast.Name):
                parsers.setdefault(target.id, set()).update(names)
    forwarding: set[str] = set()
    for node in nodes:
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and isinstance(node.func.value, ast.Name)):
            continue
        if any(keyword.arg == "nargs" and isinstance(keyword.value, ast.Attribute)
               and keyword.value.attr == "REMAINDER" for keyword in node.keywords):
            forwarding.update(parsers.get(node.func.value.id, ()))
    return frozenset(forwarding)


def cli_schema(script: Path) -> CliSchema:
    """Collect wrapper options and forwarding commands without importing the script."""
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return CliSchema()
    options: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "add_argument":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.startswith("-"):
                    options.add(arg.value)
    return CliSchema(frozenset(options), forwarding_subcommands(tree))


def main() -> int:
    failures = []
    checked = 0
    cache: dict[Path, CliSchema] = {}
    for doc in documents(ROOT):
        for line, invocation in commands(doc):
            script = resolve_script(doc, invocation[0])
            if script is None:
                continue
            if not script.is_file():
                failures.append((doc, line, f"script does not exist: {script.relative_to(ROOT)}"))
                continue
            args = invocation[1:]
            if script not in cache:
                cache[script] = cli_schema(script)
            schema = cache[script]
            checked += 1
            checked_line = line.split(" -- ", 1)[0]
            used = set(OPTION_RE.findall(checked_line)) - {"--help"}
            forwards = bool(args) and args[0] in schema.forwarding
            unknown = sorted(used - schema.options) if schema.options and not forwards else []
            if unknown:
                failures.append((doc, line, "unknown options: " + ", ".join(unknown)))

    for doc, line, error in failures:
        print(f"{doc.relative_to(ROOT)}: {error}\n  {line}")
    if failures:
        print(f"FAIL: {len(failures)} documented command errors ({checked} checked)", file=sys.stderr)
        return 1
    print(f"OK: {checked} documented Python commands match their argparse schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
