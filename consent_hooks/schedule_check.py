#!/usr/bin/env python3
"""PostToolUse / Stop hook: show the saved next run of every automation just created or updated.

Purpose: the hour in a recurrence rule is interpreted by the Codex app's scheduler, not by the
wording of the request. Writing the hour in local time when the scheduler reads UTC (or the reverse)
makes a scheduled task fire hours off. After each tool call this hook reads the scheduler's saved
next run and shows it to the agent in your time zone; with --stop it sends the reply back once so
the agent compares that time with the intended one before reporting. Any failure stays silent.

Requires the Codex desktop app's automations (the scheduler database under $CODEX_HOME/sqlite).
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402


def database() -> pathlib.Path:
    return common.codex_home() / "sqlite" / "codex-dev.db"


def time_zone(config: Dict[str, Any]):
    name = (config.get("schedule_check") or {}).get("timezone")
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name), name
        except Exception:
            pass
    local = datetime.now().astimezone().tzinfo
    return local, str(local)


def changed_automations(last_seen: int) -> Tuple[List[tuple], int]:
    with sqlite3.connect(database().resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id, name, status, rrule, next_run_at, updated_at FROM automations "
                          "WHERE updated_at > ? ORDER BY updated_at", (last_seen,)).fetchall()
        newest = db.execute("SELECT COALESCE(MAX(updated_at), 0) FROM automations").fetchone()[0]
    return rows, int(newest or 0)


def describe(row: tuple, now: datetime, zone, zone_name: str) -> str:
    automation_id, name, status, rrule, next_run_at, _ = row
    if status != "ACTIVE":
        return f"- {automation_id}「{name}」: {status}"
    if not next_run_at:
        return f"- {automation_id}「{name}」: 次回の実行なし（{rrule}）。予定が組まれていません"
    fire = datetime.fromtimestamp(next_run_at / 1000, zone)
    warning = "。すでに過ぎた時刻です" if fire < now else ""
    return f"- {automation_id}「{name}」: 次回の実行 {fire:%m/%d %H:%M}（{zone_name}）{warning}"


def decide(config: Dict[str, Any], stop: bool) -> Dict[str, Any]:
    state = common.state_dir() / "schedule_check.json"
    try:
        last_seen = int(json.loads(state.read_text()).get("updated_at", 0))
        first_run = False
    except Exception:
        last_seen, first_run = 0, True
    rows, newest = changed_automations(last_seen)
    state.write_text(json.dumps({"updated_at": max(newest, last_seen)}))
    if first_run or not rows:
        return {}  # the first run only records the current state
    zone, zone_name = time_zone(config)
    now = datetime.now(zone)
    message = ("[schedule-check] 自動実行の予定が作成・変更されました。スケジューラーが保存した次回の実行時刻です。\n"
               + "\n".join(describe(row, now, zone, zone_name) for row in rows)
               + "\n意図した時刻と違えば、報告の前に直して、この表示で一致を確かめてください。")
    if stop:
        return {"decision": "block", "reason": message}
    return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": message}}


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        event = {}
    config = common.load_config()
    if not (config.get("schedule_check") or {}).get("enabled", True) or (
            isinstance(event, dict) and (event.get("agent_id") or event.get("agent_type"))):
        print("{}")
        return
    try:
        output = decide(config, "--stop" in sys.argv)
    except Exception:
        output = {}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
