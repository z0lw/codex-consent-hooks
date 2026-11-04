#!/usr/bin/env python3
"""PostToolUse hook: commit AGENTS.md (the agent's memory) automatically after it is edited.

Purpose: keep every change to your instruction files in Git history, so an unwanted edit can be
reviewed and reverted. Only the configured file is committed, only in repositories that already
track it. Nothing is pushed. Results go to $CODEX_HOME/consent-hooks/autocommit.log.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402


def targets(config: Dict[str, Any]) -> List[Dict[str, str]]:
    configured = (config.get("autocommit") or {}).get("repos")
    if configured:
        return [t for t in configured if isinstance(t, dict) and t.get("path")]
    return [{"path": str(common.codex_home()), "file": "AGENTS.md", "message": "Auto-commit AGENTS.md"}]


def git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=20)


def commit(target: Dict[str, str]) -> str:
    repo = os.path.expanduser(target["path"])
    name = target.get("file") or "AGENTS.md"
    if not os.path.isdir(repo):
        return "missing repository"
    if git(repo, "ls-files", "--error-unmatch", "--", name).returncode != 0:
        return "skip: file not tracked"
    if git(repo, "diff", "--quiet", "--", name).returncode == 0 and \
            git(repo, "diff", "--cached", "--quiet", "--", name).returncode == 0:
        return ""
    if git(repo, "add", "--", name).returncode != 0:
        return "git add failed"
    result = git(repo, "commit", "-m", target.get("message") or ("Auto-commit " + name), "--", name)
    first = (result.stdout or result.stderr).strip().splitlines()
    return ("" if result.returncode == 0 else "git commit failed: ") + (first[0] if first else "")


def main() -> None:
    try:
        json.load(sys.stdin)
    except ValueError:
        pass
    config = common.load_config()
    if (config.get("autocommit") or {}).get("enabled", True):
        log = common.state_dir() / "autocommit.log"
        for target in targets(config):
            try:
                outcome = commit(target)
            except (OSError, subprocess.TimeoutExpired) as exc:
                outcome = type(exc).__name__
            if outcome:
                with open(log, "a", encoding="utf-8") as stream:
                    stream.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + target["path"] + ": " + outcome + "\n")
    print("{}")


if __name__ == "__main__":
    main()
