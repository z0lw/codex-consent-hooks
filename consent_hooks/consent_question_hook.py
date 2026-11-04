#!/usr/bin/env python3
"""PreToolUse hook for request_user_input: hold back questions the user has already answered.

Purpose: when the agent is about to ask the user for consent that is routine (terms,
checkboxes, bot checks, "may I proceed?") or that the user's own words already gave,
the question is not sent and the agent is told to perform the step itself. Money,
personal information, physical steps, and real choices still reach the user. Any failure
lets the question through.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402


def question_text(tool_input: Any) -> str:
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except ValueError:
            return tool_input
    parts = []
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    for item in questions or []:
        if not isinstance(item, dict):
            continue
        for key in ("title", "question", "header"):
            if isinstance(item.get(key), str):
                parts.append(item[key])
        for option in item.get("options") or []:
            label = option.get("label") if isinstance(option, dict) else option
            if isinstance(label, str):
                parts.append("- " + label)
    return "\n".join(parts)


def _deny(reason: str) -> Dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": common.relay(reason)}}


def decide(event: Dict[str, Any], user_texts: List[str], assistant_texts: List[str], config: Dict[str, Any],
           evaluator: Optional[common.Evaluator] = None) -> Dict[str, Any]:
    if event.get("agent_id") or event.get("agent_type"):
        return {}
    if "request_user_input" not in str(event.get("tool_name") or ""):
        return {}
    question = question_text(event.get("tool_input"))
    if not question.strip() or not user_texts:
        return {}
    if evaluator is not None:
        found = common.judge_authorized(question, user_texts, assistant_texts, evaluator)
        if found:
            return _deny(
                "同意します。「" + str(found.get("asked_step") or "")[:120] + "」は、私の発言「"
                + str(found.get("authorization_quote") or "")[:200] + "」で既に許可しています。"
                "この質問は送らずに実行し、結果を確認してから報告してください。"
                "許可した段階を超える確定・送信・支払い・個人情報の送信は、改めて私に確認してください。")
    reason = common.consent_reason(question, user_texts, config)
    if not reason:
        return {}
    if evaluator is not None and common.judge_reserved(question, user_texts, evaluator) != "none":
        return {}
    return _deny(reason + "この質問は送らずに、止まっている操作を先に実行してください。")


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
    history = common.read_transcript(common.find_transcript(event))
    judge = common.codex_evaluator(config) if (config.get("judge") or {}).get("enabled") else None
    try:
        output = decide(event, history["users"], history["assistants"], config, judge)
    except Exception:
        output = {}
    session, turn = str(event.get("session_id") or ""), str(event.get("turn_id") or "")
    if output and not common.take_budget(session, turn, int(config.get("max_resumes_per_turn") or 3)):
        output = {}
    if output:
        common.log_event("question_held", session)
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
