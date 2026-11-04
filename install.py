#!/usr/bin/env python3
"""Register the hooks in $CODEX_HOME/hooks.json (default ~/.codex/hooks.json).

  python3 install.py                     # every hook
  python3 install.py --skip sidebar      # leave out a hook (repeatable)
  python3 install.py --dry-run           # print the result without writing
  python3 install.py --uninstall

Hook names: consent, question, completion, language, schedule, sidebar, autocommit.
Existing hooks are kept. A backup of hooks.json is written before any change.
The sidebar commands /p, /p1 ... /p5 are copied to $CODEX_HOME/prompts unless files with those names exist.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import shutil
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent
HOOK_DIR = REPO / "consent_hooks"
NAMES = ("consent", "question", "completion", "language", "schedule", "sidebar", "autocommit")


def command(script: str, *args: str) -> str:
    return " ".join([shlex.quote(sys.executable), shlex.quote(str(HOOK_DIR / script)), *args])


def handler(script: str, timeout: int, status: str, *args: str, run_async: bool = False) -> dict:
    entry = {"type": "command", "command": command(script, *args), "timeout": timeout, "statusMessage": status}
    if run_async:
        entry["async"] = True
    return entry


def entries(skip: set) -> dict:
    wanted = {
        "consent": [("Stop", None, handler("consent_stop_hook.py", 90, "確認待ちの停止に常設の同意を返すか判定"))],
        "completion": [("Stop", None, handler("completion_guard.py", 120, "依頼が完了したかを確認"))],
        "language": [("Stop", None, handler("language_check.py", 90, "使わないと決めた表現を確認"))],
        "schedule": [("Stop", None, handler("schedule_check.py", 10, "自動実行の予定時刻を確認", "--stop")),
                     ("PostToolUse", ".*", handler("schedule_check.py", 10, "自動実行の予定時刻を確認"))],
        "sidebar": [("Stop", None, handler("sidebar_sections.py", 90, "サイドバー: 状態判定", run_async=True)),
                    ("UserPromptSubmit", None, handler("sidebar_sections.py", 15, "サイドバー: 進行中"))],
        "question": [("PreToolUse", "request_user_input.*",
                      handler("consent_question_hook.py", 60, "許可済みの確認かを判定"))],
        "autocommit": [("PostToolUse", "apply_patch|Edit|Write",
                        handler("autocommit_agents_md.py", 30, "AGENTS.md の変更を自動コミット"))],
    }
    result: dict = {}
    for name in NAMES:
        if name in skip:
            continue
        for event, matcher, hook in wanted[name]:
            group = {"hooks": [hook]}
            if matcher:
                group["matcher"] = matcher
            result.setdefault(event, []).append(group)
    return result


def ours(group: dict) -> bool:
    return any(str(HOOK_DIR) in str(h.get("command", "")) for h in group.get("hooks") or [])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME") or str(pathlib.Path.home() / ".codex"))
    parser.add_argument("--skip", action="append", default=[], choices=NAMES)
    parser.add_argument("--without-completion-check", action="store_true", help="same as --skip completion")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    skip = set(args.skip) | ({"completion"} if args.without_completion_check else set())

    home = pathlib.Path(args.codex_home).expanduser()
    hooks_path = home / "hooks.json"
    data = json.loads(hooks_path.read_text(encoding="utf-8")) if hooks_path.exists() else {}
    hooks = data.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = [group for group in hooks[event] if not ours(group)]
        if not hooks[event]:
            del hooks[event]
    if not args.uninstall:
        for event, groups in entries(skip).items():
            hooks.setdefault(event, []).extend(groups)

    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.dry_run:
        print(rendered)
        return
    home.mkdir(parents=True, exist_ok=True)
    if hooks_path.exists():
        backup = hooks_path.with_name("hooks.json.bak-consent-hooks-" + time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(hooks_path, backup)
        print("backup:", backup)
    hooks_path.write_text(rendered, encoding="utf-8")
    print("updated:", hooks_path)
    if args.uninstall:
        return
    config = home / "consent-hooks.json"
    if not config.exists():
        shutil.copy2(REPO / "config.example.json", config)
        print("created:", config)
    if "sidebar" not in skip:
        prompts = home / "prompts"
        prompts.mkdir(exist_ok=True)
        for source in sorted((REPO / "prompts").glob("*.md")):
            if not (prompts / source.name).exists():
                shutil.copy2(source, prompts / source.name)
                print("created:", prompts / source.name)
    print("Codex を再起動し、新しいフックの確認が表示されたら内容を確認して有効にしてください。")


if __name__ == "__main__":
    main()
