#!/usr/bin/env python3
"""Run one offline check with canonical built-in rules only and emit JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adduce.config import Config


_GIT_ENVIRONMENT_KEYS = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_DIR",
        "GIT_INDEX_FILE",
        "GIT_NAMESPACE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_WORK_TREE",
    }
)
_MUTATING_EVENTS = frozenset(
    {
        "os.chmod",
        "os.chown",
        "os.link",
        "os.mkdir",
        "os.remove",
        "os.rename",
        "os.rmdir",
        "os.symlink",
        "os.truncate",
        "os.utime",
    }
)


def _source_tree_sha256(package_dir: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in package_dir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    for path in files:
        relative = path.relative_to(package_dir).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _peak_rss_observation() -> dict[str, object]:
    """Return the child process peak RSS with the platform's documented unit."""
    platform_id = sys.platform
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (ImportError, OSError, ValueError):
        value = 0
    unit = (
        "bytes"
        if platform_id == "darwin"
        else "kibibytes"
        if platform_id.startswith("linux")
        else None
    )
    if value <= 0 or unit is None:
        return {
            "available": False,
            "value": None,
            "unit": "unavailable",
            "source": "unavailable",
            "platform": platform_id,
        }
    return {
        "available": True,
        "value": value,
        "unit": unit,
        "source": "resource.getrusage(RUSAGE_SELF)",
        "platform": platform_id,
    }


def _allowed_git_command(
    executable: object, arguments: object, repository: Path | None = None
) -> bool:
    """Permit only the read-only Git queries used by repository ingestion."""
    if not isinstance(executable, (str, bytes, os.PathLike)) or not isinstance(
        arguments, (list, tuple)
    ):
        return False
    try:
        executable_name = Path(os.fsdecode(executable)).name
        command = [os.fsdecode(token) for token in arguments]
    except (TypeError, ValueError):
        return False
    if executable_name != "git" or not command or Path(command[0]).name != "git":
        return False
    if len(command) < 4 or command[1] != "-C":
        return False
    if repository is not None and Path(command[2]).resolve() != repository:
        return False
    operation = tuple(command[3:])
    return operation in {
        ("rev-parse", "--is-inside-work-tree"),
        ("rev-parse", "HEAD"),
        ("tag", "--list"),
        ("ls-files",),
        ("remote", "-v"),
    }


def _enforce_offline(event: str, args: tuple[object, ...], repository: Path | None = None) -> None:
    if event.startswith("socket."):
        raise RuntimeError(f"network access is disabled during corpus scans ({event})")
    if event == "subprocess.Popen":
        executable = args[0] if args else None
        arguments = args[1] if len(args) > 1 else None
        if not _allowed_git_command(executable, arguments, repository):
            raise RuntimeError(f"process execution is disabled during corpus scans ({event})")
    if event == "os.posix_spawn":
        executable = args[0] if args else None
        arguments = args[1] if len(args) > 1 else None
        if not _allowed_git_command(executable, arguments, repository):
            raise RuntimeError(f"process execution is disabled during corpus scans ({event})")
    if event in {"os.fork", "os.forkpty", "os.system", "pty.spawn"} or event.startswith(
        ("os.exec", "os.spawn")
    ):
        raise RuntimeError(f"process execution is disabled during corpus scans ({event})")
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        write_mode = isinstance(mode, str) and any(token in mode for token in "wax+")
        write_flags = isinstance(flags, int) and bool(
            flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        )
        if write_mode or write_flags:
            raise RuntimeError("filesystem writes are disabled during corpus scans (open)")
    if event in _MUTATING_EVENTS:
        raise RuntimeError(f"filesystem mutation is disabled during corpus scans ({event})")


def _default_config(root: Path) -> Config:
    """Ignore repository-authored Adduce settings for a uniform corpus baseline."""
    from adduce.config import Config

    del root
    return Config()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    args = parser.parse_args()
    repository = args.repository.resolve()
    source_root = os.environ.pop("ADDUCE_CORPUS_SOURCE_ROOT", "")
    expected_source_sha256 = os.environ.pop("ADDUCE_CORPUS_SOURCE_TREE_SHA256", "")
    if not source_root or not expected_source_sha256:
        print("exact Adduce source identity is required for corpus scans", file=sys.stderr)
        return 2
    for key in list(os.environ):
        if key in _GIT_ENVIRONMENT_KEYS or key.startswith("GIT_CONFIG_"):
            os.environ.pop(key, None)
    os.environ.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "PATH": os.defpath,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
        }
    )
    git = shutil.which("git")
    if git is None:
        print("Git is required for attributable corpus scans", file=sys.stderr)
        return 2
    os.environ["PATH"] = str(Path(git).resolve().parent)
    sys.addaudithook(lambda event, event_args: _enforce_offline(event, event_args, repository))
    try:
        source_path = Path(source_root).resolve(strict=True)
        sys.path.insert(0, str(source_path))
        import adduce
        import adduce.engine as engine
        from adduce.report.json_report import render
        from adduce.rules import discover_rules

        package_dir = Path(adduce.__file__).resolve().parent
        if package_dir != source_path / "adduce":
            raise RuntimeError("loaded Adduce package does not match the selected source tree")
        observed_source_sha256 = _source_tree_sha256(package_dir)
        if observed_source_sha256 != expected_source_sha256:
            raise RuntimeError("Adduce source bytes changed before scanner import")
        engine.load_config = _default_config
        rules = discover_rules(include_plugins=False)
        result = engine.run_check(repository, include_plugins=False, rules=rules)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    payload = json.loads(render(result))
    payload["repository"]["input_file_count"] = len(result.repo.files)
    payload["repository"]["input_byte_count"] = sum(entry.size for entry in result.repo.files)
    observed_rule_ids = {finding["rule_id"] for finding in payload["findings"]}
    for rule in rules:
        if rule.id in observed_rule_ids:
            continue
        if rule.applies_to(result.repo):
            raise RuntimeError(f"applicable built-in rule emitted no finding: {rule.id}")
        payload["findings"].append(
            {
                "rule_id": rule.id,
                "category": rule.category.value,
                "title": rule.title,
                "status": "not-applicable",
                "confidence": 1.0,
                "severity": rule.effective_severity,
                "message": "Rule not applicable to this repository snapshot.",
                "remediation": "",
                "weight": rule.weight,
                "locations": [],
                "fix_command": rule.fix_command,
                "suppressed": False,
            }
        )
    payload["corpus_execution"] = {
        "configuration_mode": "defaults-only-repository-config-disabled",
        "plugins_enabled": False,
        "network_policy": "python-audit-socket-deny",
        "process_policy": "python-audit-read-only-git-metadata-only",
        "enforcement_scope": "scanner-regression-guard-not-os-sandbox",
        "environment_policy": "minimal-no-host-credentials",
        "input_policy": "clone-root-symlink-containment",
        "adduce_source_tree_sha256": observed_source_sha256,
        "peak_rss": _peak_rss_observation(),
    }
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
