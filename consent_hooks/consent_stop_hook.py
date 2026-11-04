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
import re
import sqlite3
import subprocess
import sys
import time
import uuid
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


def _wait_for(transcript: pathlib.Path, marker: str, seconds: int) -> bool:
    for _ in range(seconds):
        if common.transcript_contains(transcript, marker):
            return True
        time.sleep(1)
    return common.transcript_contains(transcript, marker)


def resume_command(codex: str, session: str, transcript: pathlib.Path) -> Optional[List[str]]:
    """Rebuild `codex exec resume` with the thread's own folder, sandbox, approval, and model.

    Without the thread's folder the CLI starts in the hook's folder and can exit before resuming."""
    context: Dict[str, Any] = {}
    try:
        with open(transcript, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if '"turn_context"' in line:
                    item = json.loads(line)
                    if item.get("type") == "turn_context":
                        context = item.get("payload") or {}
    except (OSError, ValueError):
        return None
    cwd = context.get("cwd")
    policy = context.get("sandbox_policy") or {}
    mode = policy.get("type")
    if mode == "disabled":
        mode = "danger-full-access"
    if not (isinstance(cwd, str) and os.path.isdir(cwd)) or mode not in {"read-only", "workspace-write",
                                                                         "danger-full-access"}:
        return None  # never fall back to a broader default than the thread had
    command = [codex, "exec", "-C", cwd, "-s", mode]
    if context.get("approval_policy"):
        command += ["-c", "approval_policy=" + json.dumps(context["approval_policy"])]
    if mode == "workspace-write":
        for key in ("writable_roots", "network_access", "exclude_tmpdir_env_var", "exclude_slash_tmp"):
            if key in policy:
                command += ["-c", "sandbox_workspace_write." + key + "=" + json.dumps(policy[key])]
    if context.get("model"):
        command += ["-m", str(context["model"])]
    if context.get("effort"):
        command += ["-c", "model_reasoning_effort=" + json.dumps(context["effort"])]
    return command + ["resume", "--skip-git-repo-check", session, "-"]


def _take_queued_item(item_id: str, session: str):
    """Detach our own queued item before resuming directly, so the consent is not sent twice."""
    database = common.codex_home() / "queue_1.sqlite"
    if not database.exists():
        return None
    with sqlite3.connect(str(database), timeout=5) as db:
        row = db.execute("SELECT * FROM queued_items WHERE id=? AND thread_id=?", (item_id, session)).fetchone()
        if row is not None:
            db.execute("DELETE FROM queued_items WHERE id=? AND thread_id=?", (item_id, session))
    return row


def _restore_queued_item(row) -> None:
    with sqlite3.connect(str(common.codex_home() / "queue_1.sqlite"), timeout=5) as db:
        db.execute("INSERT OR IGNORE INTO queued_items VALUES (" + ",".join("?" * len(row)) + ")", row)


def deliver(job: Dict[str, Any]) -> None:
    """Background worker: send the consent as a user message and confirm it reached the thread.

    1. `codex queue --resume` (a thread open in the Codex app or on a local daemon takes it).
    2. If the thread cannot be resumed there, detach that queued item and resume the thread with
       `codex exec resume` using the thread's own settings. Restore the item if that fails."""
    session, text, transcript = job["session"], job["text"], pathlib.Path(job["transcript"])
    marker, codex = job["marker"], job["codex"]
    try:
        result = subprocess.run([codex, "queue", "--thread", session, "--resume", "--message", text],
                                capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        common.log_event("queue_failed", session, type(exc).__name__)
        return
    output = result.stdout + result.stderr
    if result.returncode != 0:
        common.log_event("queue_failed", session, "exit_" + str(result.returncode))
        return
    if _wait_for(transcript, marker, 15 if "could not resume" in output else 30):
        common.log_event("queue_delivered", session)
        return
    queued = re.search(r"Queued message ([0-9A-Za-z-]+) for thread", output)
    command = resume_command(codex, session, transcript)
    if "could not resume" not in output or not queued or command is None:
        common.log_event("queue_pending", session, "left_in_queue")  # arrives when the thread next resumes
        return
    try:
        row = _take_queued_item(queued.group(1), session)
    except sqlite3.Error:
        common.log_event("queue_pending", session, "queue_unreadable")
        return
    if row is None:
        common.log_event("queue_pending", session, "queue_item_consumed")
        return
    env = dict(os.environ)
    env[common.ACTIVE_ENV] = ""  # the resumed turn runs the hooks normally
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, start_new_session=True, env=env)
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()
        while True:
            if common.transcript_contains(transcript, marker):
                common.log_event("exec_resume_delivered", session)
                return  # leave the resumed turn running
            if process.poll() is not None:
                common.log_event("exec_resume_failed", session, "exit_" + str(process.returncode))
                break
            time.sleep(1)
    except OSError as exc:
        common.log_event("exec_resume_failed", session, type(exc).__name__)
    if not common.transcript_contains(transcript, marker):
        try:
            _restore_queued_item(row)
            common.log_event("queue_pending", session, "queue_restored")
        except sqlite3.Error:
            common.log_event("queue_restore_failed", session)


def schedule_queue_delivery(event: Dict[str, Any], reason: str, config: Dict[str, Any]) -> bool:
    session = str(event.get("session_id") or "")
    transcript = common.find_transcript(event)
    codex = str(config.get("codex_path") or "codex")
    if not session or transcript is None or not common.queue_supported(codex):
        return False
    marker = "[delivery:" + uuid.uuid4().hex[:12] + "]"
    job = {"session": session, "transcript": str(transcript), "codex": codex, "marker": marker,
           "text": common.relay(reason) + "\n" + marker}
    try:
        process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve()), "--deliver"],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        process.stdin.write(json.dumps(job, ensure_ascii=False).encode("utf-8"))
        process.stdin.close()
    except OSError:
        return False
    common.log_event("queue_scheduled", session)
    return True


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
        if config.get("delivery") == "queue" and schedule_queue_delivery(event, output["reason"][len(common.HEADER) + 1:], config):
            output = {}  # the consent arrives as the next user message instead of a hook continuation
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    if sys.argv[1:] == ["--deliver"]:
        deliver(json.load(sys.stdin))
    else:
        main()
