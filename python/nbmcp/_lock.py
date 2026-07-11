from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import tomllib
from datetime import datetime
from typing import Any

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


def _file_record(path: pathlib.Path) -> dict[str, str]:
    return {
        "path": str(path),
        "sha256": _sha256(path),
    }


def _project_metadata(project_dir: pathlib.Path) -> dict[str, Any]:
    pyproject = project_dir / "pyproject.toml"
    cargo = project_dir / "Cargo.toml"
    return {
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "project_root": str(project_dir.resolve()),
        "pyproject": _file_record(pyproject),
        "cargo_toml": _file_record(cargo),
    }


def generate_lock(output: str = "nbmcp.lock", project_dir: str = ".") -> int:
    project_path = pathlib.Path(project_dir).resolve()
    lock_path = pathlib.Path(output).resolve()
    metadata = _project_metadata(project_path)
    lock_data = {
        "lockVersion": LOCK_VERSION,
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "metadata": metadata,
        "transport": "stdio",
        "server": {
            "name": project_path.name,
            "description": "nbmcp server lock file",
        },
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

    errors = []
    if metadata.get("python_version") != sys.version.split()[0]:
        errors.append(
            f"Python version mismatch: lock={metadata.get('python_version')} current={sys.version.split()[0]}"
        )
    if metadata.get("platform") != sys.platform:
        errors.append(
            f"Platform mismatch: lock={metadata.get('platform')} current={sys.platform}"
        )

    for record in (pyproject_record, cargo_record):
        if not record:
            errors.append("Invalid lock file metadata")
            continue
        path = pathlib.Path(record["path"])
        if not path.exists():
            errors.append(f"Missing file from lock: {path}")
            continue
        actual = _sha256(path)
        if actual != record["sha256"]:
            errors.append(f"Hash mismatch for {path}: lock={record['sha256']} current={actual}")

    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"nbmcp lock verification failed: {len(errors)} issue(s)", file=sys.stderr)
        return 1

    print(f"nbmcp lock verified: {lock_path}")
    return 0
