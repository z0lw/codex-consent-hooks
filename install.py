#!/usr/bin/env python3
"""Register the hooks in $CODEX_HOME/hooks.json (default ~/.codex/hooks.json).

  python3 install.py                       # consent hooks + completion check
  python3 install.py --without-completion-check
  python3 install.py --dry-run             # print the result without writing
  python3 install.py --uninstall

Existing hooks are kept. A backup of hooks.json is written before any change.
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


def command(script: str) -> str:
    return shlex.quote(sys.executable) + " " + shlex.quote(str(HOOK_DIR / script))


def ours(entry: dict) -> bool:
    return any(str(HOOK_DIR) in str(h.get("command", "")) or "consent_hooks/" in str(h.get("command", ""))
               for h in entry.get("hooks") or [])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME") or str(pathlib.Path.home() / ".codex"))
    parser.add_argument("--without-completion-check", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    home = pathlib.Path(args.codex_home).expanduser()
    hooks_path = home / "hooks.json"
    data = json.loads(hooks_path.read_text(encoding="utf-8")) if hooks_path.exists() else {}
    hooks = data.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = [entry for entry in hooks[event] if not ours(entry)]
        if not hooks[event]:
            del hooks[event]

    if not args.uninstall:
        stop = [{"hooks": [{"type": "command", "command": command("consent_stop_hook.py"), "timeout": 90,
                            "statusMessage": "確認待ちの停止に常設の同意を返すか判定"}]}]
        if not args.without_completion_check:
            stop.append({"hooks": [{"type": "command", "command": command("completion_guard.py"), "timeout": 120,
                                    "statusMessage": "依頼が完了したかを確認"}]})
        hooks.setdefault("Stop", []).extend(stop)
        hooks.setdefault("PreToolUse", []).append(
            {"matcher": "request_user_input.*",
             "hooks": [{"type": "command", "command": command("consent_question_hook.py"), "timeout": 60,
                        "statusMessage": "許可済みの確認かを判定"}]})

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

    config = home / "consent-hooks.json"
    if not args.uninstall and not config.exists():
        shutil.copy2(REPO / "config.example.json", config)
        print("created:", config)
    if not args.uninstall:
        print("Codex を再起動し、新しいフックの確認が表示されたら内容を確認して有効にしてください。")


if __name__ == "__main__":
    main()
