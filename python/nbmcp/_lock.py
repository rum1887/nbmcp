from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tomllib
from datetime import datetime
from typing import Any, Optional

LOCK_VERSION = "nbmcp-lock-v1"


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as infile:
        for chunk in iter(lambda: infile.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_toml(path: pathlib.Path) -> dict[str, Any]:
    with path.open("rb") as infile:
        return tomllib.load(infile)


def _should_ignore(path: pathlib.Path) -> bool:
    return any(part in {"target", ".git", "__pycache__"} for part in path.parts)


def _collect_project_files(project_dir: pathlib.Path) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file() or _should_ignore(path):
            continue
        if path.suffix in {".py", ".toml", ".md"}:
            files.append(path)
    return files


def _file_record(path: pathlib.Path, base: Optional[pathlib.Path] = None) -> dict[str, str]:
    relative_path = str(path.relative_to(base)) if base is not None else str(path)
    return {
        "path": relative_path,
        "sha256": _sha256(path),
    }


def _project_metadata(project_dir: pathlib.Path) -> dict[str, Any]:
    pyproject = project_dir / "pyproject.toml"
    cargo = project_dir / "Cargo.toml"
    return {
        "project_root": ".",
        "pyproject": _file_record(pyproject, project_dir),
        "cargo_toml": _file_record(cargo, project_dir),
    }


def generate_lock(
    output: str = "nbmcp.lock",
    project_dir: str = ".",
    transport: str = "stdio",
    address: str = "",
    server_name: Optional[str] = None,
    description: str = "nbmcp server lock file",
) -> int:
    project_path = pathlib.Path(project_dir).resolve()
    lock_path = pathlib.Path(output).resolve()
    metadata = _project_metadata(project_path)
    if transport not in {"stdio", "http"}:
        raise ValueError("transport must be one of 'stdio' or 'http'")

    transport_data: dict[str, str] = {"type": transport}
    if address:
        transport_data["address"] = address

    file_records = [_file_record(path, project_path) for path in _collect_project_files(project_path)]
    lock_data = {
        "lockVersion": LOCK_VERSION,
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "metadata": metadata,
        "transport": transport_data,
        "server": {
            "name": server_name or project_path.name,
            "description": description,
        },
        "files": file_records,
    }
    lock_path.write_text(json.dumps(lock_data, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {lock_path}")
    return 0


def verify_lock(lock_file: str = "nbmcp.lock") -> int:
    lock_path = pathlib.Path(lock_file).resolve()
    if not lock_path.exists():
        print(f"Lock file not found: {lock_path}", file=sys.stderr)
        return 1

    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    if lock_data.get("lockVersion") != LOCK_VERSION:
        print(
            f"Unsupported lock version: {lock_data.get('lockVersion')}",
            file=sys.stderr,
        )
        return 1

    metadata = lock_data.get("metadata", {})
    project_root = pathlib.Path(metadata.get("project_root", "."))
    pyproject_record = metadata.get("pyproject", {})
    cargo_record = metadata.get("cargo_toml", {})
    files = lock_data.get("files", [])

    if not project_root.is_absolute():
        project_root = pathlib.Path.cwd() / project_root

    errors = []

    for record in (pyproject_record, cargo_record):
        if not record:
            errors.append("Invalid lock file metadata")
            continue
        path = pathlib.Path(record["path"])
        if not path.is_absolute():
            path = project_root / path
        if not path.exists():
            errors.append(f"Missing file from lock: {path}")
            continue
        actual = _sha256(path)
        if actual != record["sha256"]:
            errors.append(f"Hash mismatch for {path}: lock={record['sha256']} current={actual}")

    if not isinstance(files, list):
        errors.append("Invalid lock file contents: missing files list")
    else:
        for record in files:
            if not isinstance(record, dict):
                errors.append("Invalid lock file contents: malformed file record")
                continue
            path_text = record.get("path")
            expected_hash = record.get("sha256")
            if not path_text or not expected_hash:
                errors.append("Invalid lock file contents: malformed file record")
                continue
            path = pathlib.Path(path_text)
            if not path.is_absolute():
                path = project_root / path
            if not path.exists():
                errors.append(f"Missing file from lock: {path}")
                continue
            actual = _sha256(path)
            if actual != expected_hash:
                errors.append(f"Hash mismatch for {path}: lock={expected_hash} current={actual}")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"nbmcp lock verification failed: {len(errors)} issue(s)", file=sys.stderr)
        return 1

    print(f"nbmcp lock verified: {lock_path}")
    return 0
