from __future__ import annotations

import argparse
import ast
import hashlib
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from ._lock import generate_lock, verify_lock

PRIMITIVE_TYPES = {"str", "int", "float", "bool"}

@dataclass
class ToolCandidate:
    name: str
    path: pathlib.Path
    lineno: int
    has_docstring: bool
    has_description: bool
    ambiguous_params: List[str]


def _is_tool_decorator(decorator: ast.expr) -> bool:
    if isinstance(decorator, ast.Call):
        func = decorator.func
    else:
        func = decorator

    if isinstance(func, ast.Name):
        return func.id == "tool"

    if isinstance(func, ast.Attribute):
        return func.attr == "tool"

    return False


def _extract_description(decorator: ast.expr) -> bool:
    if not isinstance(decorator, ast.Call):
        return False

    for kw in decorator.keywords:
        if kw.arg == "description" and isinstance(kw.value, ast.Constant):
            return isinstance(kw.value.value, str) and bool(kw.value.value.strip())

    return False


def _annotation_is_ambiguous(annotation: Optional[ast.expr]) -> bool:
    if annotation is None:
        return True

    if isinstance(annotation, ast.Name):
        return annotation.id not in PRIMITIVE_TYPES

    if isinstance(annotation, ast.Attribute):
        return annotation.attr not in PRIMITIVE_TYPES

    if isinstance(annotation, ast.Subscript):
        value = annotation.value
        if isinstance(value, ast.Name):
            name = value.id
        elif isinstance(value, ast.Attribute):
            name = value.attr
        else:
            return True

        if name in {"list", "List"}:
            slice_value = getattr(annotation.slice, "value", annotation.slice)
            return _annotation_is_ambiguous(slice_value)

        if name in {"dict", "Dict"}:
            return True

        if name in {"Optional", "Union"}:
            slice_value = annotation.slice
            if isinstance(slice_value, ast.Tuple):
                args = list(slice_value.elts)
            else:
                args = [slice_value]
            non_none = [arg for arg in args if not (isinstance(arg, ast.NameConstant) and arg.value is None)]
            if len(non_none) == 1:
                return _annotation_is_ambiguous(non_none[0])
            return True

        return True

    return True


def _collect_tools_from_ast(tree: ast.AST, path: pathlib.Path) -> List[ToolCandidate]:
    tools: List[ToolCandidate] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        for decorator in node.decorator_list:
            if not _is_tool_decorator(decorator):
                continue

            has_description = _extract_description(decorator)
            has_docstring = ast.get_docstring(node) is not None
            ambiguous_params: List[str] = []

            for arg in node.args.args:
                if arg.arg == "self":
                    continue
                if _annotation_is_ambiguous(arg.annotation):
                    ambiguous_params.append(arg.arg)

            tools.append(
                ToolCandidate(
                    name=node.name,
                    path=path,
                    lineno=node.lineno,
                    has_docstring=has_docstring,
                    has_description=has_description,
                    ambiguous_params=ambiguous_params,
                )
            )
            break

    return tools


def _scan_file(path: pathlib.Path) -> Tuple[List[ToolCandidate], List[str]]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    return _collect_tools_from_ast(tree, path), []


def _resolve_paths(paths: Iterable[str]) -> List[pathlib.Path]:
    result: List[pathlib.Path] = []
    for path_text in paths:
        path = pathlib.Path(path_text)
        if path.is_dir():
            for child in sorted(path.rglob("*.py")):
                result.append(child)
        elif path.is_file() and path.suffix == ".py":
            result.append(path)
        else:
            raise FileNotFoundError(f"Path not found or unsupported: {path}")
    return result


def run_check(paths: Optional[List[str]] = None) -> int:
    paths = paths or ["."]
    files = _resolve_paths(paths)
    tools: List[ToolCandidate] = []
    issues: List[str] = []

    for path in files:
        try:
            file_tools, _ = _scan_file(path)
            tools.extend(file_tools)
        except SyntaxError as exc:
            issues.append(f"{path}:{exc.lineno}: syntax error: {exc.msg}")

    seen_names: dict[str, pathlib.Path] = {}
    for tool in tools:
        if not tool.has_description and not tool.has_docstring:
            issues.append(
                f"{tool.path}:{tool.lineno}: tool '{tool.name}' is missing a description and docstring"
            )

        if tool.ambiguous_params:
            issues.append(
                f"{tool.path}:{tool.lineno}: tool '{tool.name}' has ambiguous parameter(s): {', '.join(tool.ambiguous_params)}"
            )

        if tool.name in seen_names:
            issues.append(
                f"{tool.path}:{tool.lineno}: duplicate tool name '{tool.name}' (also defined in {seen_names[tool.name]})"
            )
        else:
            seen_names[tool.name] = tool.path

    for issue in issues:
        print(issue, file=sys.stderr)

    if issues:
        print(f"nbmcp check failed: {len(issues)} issue(s) found", file=sys.stderr)
        return 1

    print(f"nbmcp check passed: {len(tools)} tool(s) inspected")
    return 0


import argparse


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nbmcp")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Lint nbmcp tool definitions")
    check_parser.add_argument("paths", nargs="*", default=["."])

    lock_parser = subparsers.add_parser("lock", help="Manage nbmcp lock files")
    lock_subparsers = lock_parser.add_subparsers(dest="lock_command", required=True)

    generate_parser = lock_subparsers.add_parser("generate", help="Generate a lock file")
    generate_parser.add_argument("output", nargs="?", default="nbmcp.lock")
    generate_parser.add_argument("project_dir", nargs="?", default=".")

    verify_parser = lock_subparsers.add_parser("verify", help="Verify a lock file")
    verify_parser.add_argument("lockfile", nargs="?", default="nbmcp.lock")

    args = parser.parse_args(argv)
    if args.command == "check":
        return run_check(args.paths)
    if args.command == "lock":
        if args.lock_command == "generate":
            return generate_lock(args.output, args.project_dir)
        if args.lock_command == "verify":
            return verify_lock(args.lockfile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
