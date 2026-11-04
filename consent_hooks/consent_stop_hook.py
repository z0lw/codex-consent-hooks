#!/usr/bin/env python3
"""Stop hook: answer a stop that only waits for routine consent with the user's standing consent.

Purpose: when the agent ends its turn asking "may I accept the terms / tick the box /
solve the bot check / proceed?", reply with the consent the user configured in advance
so the agent continues the requested work itself. Money, personal information, physical
steps, and holds the user asked for still reach the user.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402

QUIET_VERDICTS = {"money", "personal_info", "user_hold", "user_only_physical", "unknown"}


def decide(event: Dict[str, Any], user_texts: List[str], config: Dict[str, Any],
           evaluator: Optional[common.Evaluator] = None) -> Dict[str, Any]:
    """Return the hook output for one Stop event (without the per-turn budget)."""
    if event.get("agent_id") or event.get("agent_type"):
        return {}  # sub-agents report to their parent, not to the user
    final = event.get("last_assistant_message") or ""
    if not final.strip() or not user_texts:
        return {}  # without the user's words, reserved checks cannot be excluded
    reason = common.consent_reason(final, user_texts, config)
    if not reason:
        return {}
    if evaluator is not None and common.judge_reserved(final, user_texts, evaluator) in QUIET_VERDICTS:
        return {}
    return {"decision": "block", "reason": common.relay(reason)}


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        print("{}")
        return
    if os.environ.get(common.ACTIVE_ENV) or not isinstance(event, dict):
        print("{}")
        return
    config = common.load_config()
    history = common.read_transcript(common.find_transcript(event), event.get("turn_id"))
    if not (event.get("last_assistant_message") or "").strip():
        event["last_assistant_message"] = history["turn_final"]
    judge = common.codex_evaluator(config) if (config.get("judge") or {}).get("enabled") else None
    output = decide(event, history["users"], config, judge)
    session, turn = str(event.get("session_id") or ""), str(event.get("turn_id") or "")
    if output and not common.take_budget(session, turn, int(config.get("max_resumes_per_turn") or 3)):
        common.log_event("consent_budget_exhausted", session)
        output = {}
    if output:
        common.log_event("consent_answered", session)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
