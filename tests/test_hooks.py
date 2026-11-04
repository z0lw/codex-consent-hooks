#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "consent_hooks"))
import common  # noqa: E402
import completion_guard  # noqa: E402
import consent_question_hook  # noqa: E402
import consent_stop_hook  # noqa: E402

REQUEST = ["サイトに登録して、使い方を調べてまとめてください。"]
CONFIG = common.load_config()


def stop(final, users=None, evaluator=None):
    event = {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t", "last_assistant_message": final}
    return consent_stop_hook.decide(event, REQUEST if users is None else users, CONFIG, evaluator)


def rollout(path, records):
    with open(path, "w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def message(role, text):
    return {"type": "response_item", "payload": {"type": "message", "role": role,
                                                 "content": [{"type": "input_text", "text": text}]}}


class TempHome(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = self.temp.name

    def tearDown(self):
        if self.old is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.old
        self.temp.cleanup()


class ConsentStopTests(TempHome):
    def test_routine_consent_stops_are_answered(self):
        for final in ("登録画面の利用規約に同意していただけますか。",
                      "チェック欄にチェックしてよろしいでしょうか。",
                      "ロボット確認のパズルが表示されました。操作してよいですか。",
                      "続行の確認をお待ちしています。"):
            out = stop(final)
            self.assertEqual(out.get("decision"), "block", final)
            self.assertTrue(out["reason"].startswith(common.RELAY_MARKER))
            self.assertIn("同意します。", out["reason"])

    def test_money_personal_info_and_physical_steps_reach_the_user(self):
        for final in ("年会費の契約になります。同意してよろしいでしょうか。",
                      "電話番号を送信してよろしいでしょうか。",
                      "どのアドレスから送信するか、ご確認をお願いできますか。",
                      "ケーブルを挿していただけますか。"):
            self.assertEqual(stop(final), {}, final)

    def test_user_holds_are_respected(self):
        self.assertEqual(stop("続行してよろしいでしょうか。", REQUEST + ["いったん待って"]), {})
        self.assertEqual(stop("続行してよろしいでしょうか。", ["申込は送信しないで、直前まで進めて"]), {})

    def test_reports_and_quoted_text_are_not_consent_stops(self):
        self.assertEqual(stop("登録を完了し、使い方を下にまとめました。"), {})
        self.assertEqual(stop("画面には「同意してよろしいですか」と表示されていました。登録は完了しています。"), {})

    def test_without_user_words_the_hook_stays_quiet(self):
        self.assertEqual(stop("利用規約に同意していただけますか。", users=[]), {})

    def test_judge_reservation_and_failure_keep_the_question(self):
        self.assertEqual(stop("利用規約に同意していただけますか。", evaluator=lambda p, s: {"reserved_wait": "money"}), {})

        def broken(prompt, schema):
            raise common.JudgeError("timeout")
        self.assertEqual(stop("利用規約に同意していただけますか。", evaluator=broken), {})
        ok = stop("利用規約に同意していただけますか。", evaluator=lambda p, s: {"reserved_wait": "none"})
        self.assertEqual(ok.get("decision"), "block")

    def test_budget_limits_answers_per_turn(self):
        self.assertTrue(all(common.take_budget("s", "t", 3) for _ in range(3)))
        self.assertFalse(common.take_budget("s", "t", 3))
        self.assertTrue(common.take_budget("s", "other-turn", 3))


class TranscriptTests(TempHome):
    def test_injected_context_and_relays_are_not_user_words(self):
        path = pathlib.Path(self.temp.name) / "rollout.jsonl"
        rollout(path, [
            message("user", "<environment_context>cwd</environment_context>"),
            message("user", "<recommended_plugins>\n- plugin\n</recommended_plugins>\n<environment_context>\n  <cwd>/x</cwd>\n</environment_context>"),
            message("user", "<permissions instructions>rules</permissions instructions>"),
            message("user", "# AGENTS.md instructions\n<INSTRUCTIONS>rules</INSTRUCTIONS>"),
            message("user", REQUEST[0]),
            message("user", common.relay("同意します。")),
            {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t2"}},
            message("assistant", "利用規約に同意していただけますか。"),
        ])
        history = common.read_transcript(path, "t2")
        self.assertEqual(history["users"], REQUEST)
        self.assertEqual(history["turn_final"], "利用規約に同意していただけますか。")

    def test_stop_hook_process_end_to_end(self):
        path = pathlib.Path(self.temp.name) / "rollout.jsonl"
        rollout(path, [{"type": "event_msg", "payload": {"type": "task_started", "turn_id": "t"}},
                       message("user", REQUEST[0])])
        event = {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t", "transcript_path": str(path),
                 "last_assistant_message": "利用規約に同意していただけますか。"}
        env = dict(os.environ, CODEX_HOME=self.temp.name)
        result = subprocess.run([sys.executable, str(REPO / "consent_hooks" / "consent_stop_hook.py")],
                                input=json.dumps(event), capture_output=True, text=True, env=env, check=True)
        self.assertEqual(json.loads(result.stdout)["decision"], "block")
        env[common.ACTIVE_ENV] = "1"
        nested = subprocess.run([sys.executable, str(REPO / "consent_hooks" / "consent_stop_hook.py")],
                                input=json.dumps(event), capture_output=True, text=True, env=env, check=True)
        self.assertEqual(json.loads(nested.stdout), {})


class QuestionTests(TempHome):
    def event(self, title):
        return {"session_id": "s", "turn_id": "t", "tool_name": "request_user_input",
                "tool_input": {"questions": [{"title": title, "options": [{"label": "はい"}, {"label": "いいえ"}]}]}}

    def test_routine_consent_question_is_held(self):
        out = consent_question_hook.decide(self.event("利用規約に同意してよろしいですか？"), REQUEST, [], CONFIG)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_real_choices_and_reserved_steps_reach_the_user(self):
        self.assertEqual(consent_question_hook.decide(self.event("AとBのどちらのプランにしますか？"), REQUEST, [], CONFIG), {})
        self.assertEqual(consent_question_hook.decide(self.event("住所を送信してよろしいですか？"), REQUEST, [], CONFIG), {})

    def test_authorization_must_quote_the_users_own_words(self):
        users = ["問い合わせフォームの送信までお願いします。"]
        answer = {"asked_step": "フォーム送信", "already_authorized": True, "reason": ""}
        found = consent_question_hook.decide(self.event("フォームを送信しますか？"), users, [], CONFIG,
                                             lambda p, s: dict(answer, authorization_quote="フォームの送信までお願いします"))
        self.assertIn("フォームの送信までお願いします", found["hookSpecificOutput"]["permissionDecisionReason"])
        invented = consent_question_hook.decide(self.event("フォームを送信しますか？"), users, [], CONFIG,
                                                lambda p, s: dict(answer, authorization_quote="何でも送ってよい"))
        self.assertEqual(invented, {})


class CompletionTests(TempHome):
    def history(self, final):
        return {"entries": [{"turn": 1, "role": "user", "text": REQUEST[0]},
                            {"turn": 1, "role": "assistant", "text": final}], "current_turn": 1}

    def verdict(self, **overrides):
        result = {"requirement_review": "r", "decision": "continue", "allow_status": "continue", "allow_basis": "",
                  "reserved_wait": "none", "gap_type": "announced_not_done", "missing_requirement": "使い方のまとめ",
                  "source_quote": "使い方を調べてまとめてください", "evidence_gap": "まとめがない",
                  "next_authorized_action": "使い方を調べてまとめる"}
        result.update(overrides)
        return result

    def run_guard(self, final, verdict, reserved="none", attempt=1):
        def evaluator(prompt, schema):
            if schema is common.RESERVED_SCHEMA:
                return {"reserved_wait": reserved}
            if isinstance(verdict, Exception):
                raise verdict
            return verdict
        event = {"session_id": "s", "turn_id": "t", "last_assistant_message": final}
        return completion_guard.decide(event, self.history(final), CONFIG, evaluator, attempt)

    def test_unfinished_work_is_sent_back_with_the_missing_item(self):
        out = self.run_guard("登録しました。使い方はこれから確認します。", self.verdict())
        self.assertEqual(out["decision"], "block")
        self.assertIn("足りないもの: 使い方のまとめ", out["reason"])
        self.assertIn("3回中1回目", out["reason"])
        last = self.run_guard("登録しました。", self.verdict(), attempt=3)
        self.assertIn("最後の差し戻し", last["reason"])

    def test_unsupported_quotes_reserved_steps_and_failures_do_not_push(self):
        self.assertEqual(self.run_guard("登録しました。", self.verdict(source_quote="全部やって")), {})
        self.assertEqual(self.run_guard("登録しました。", self.verdict(), reserved="money"), {})
        self.assertEqual(self.run_guard("登録しました。", self.verdict(reserved_wait="personal_info")), {})
        self.assertEqual(self.run_guard("登録しました。", RuntimeError("timeout")), {})

    def test_completed_work_and_consent_stops_end_quietly(self):
        done = self.verdict(decision="allow", allow_status="complete", allow_basis="まとめ済み", gap_type="none",
                            missing_requirement="", source_quote="", evidence_gap="", next_authorized_action="")
        self.assertEqual(self.run_guard("登録し、使い方をまとめました。", done), {})
        self.assertEqual(self.run_guard("利用規約に同意していただけますか。", self.verdict()), {})

    def test_tool_call_written_as_text_is_sent_back(self):
        out = self.run_guard("<tool_call><function=exec_command>", self.verdict())
        self.assertIn("実行されていません", out["reason"])


class InstallTests(unittest.TestCase):
    def test_install_keeps_existing_hooks_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as home:
            existing = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}
            pathlib.Path(home, "hooks.json").write_text(json.dumps(existing))
            for _ in range(2):
                subprocess.run([sys.executable, str(REPO / "install.py"), "--codex-home", home],
                               check=True, capture_output=True)
            data = json.loads(pathlib.Path(home, "hooks.json").read_text())
            stop_commands = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
            self.assertEqual(stop_commands[0], "other")
            self.assertEqual(len(stop_commands), 3)
            self.assertEqual(len(data["hooks"]["PreToolUse"]), 1)
            self.assertTrue(pathlib.Path(home, "consent-hooks.json").exists())
            subprocess.run([sys.executable, str(REPO / "install.py"), "--codex-home", home, "--uninstall"],
                           check=True, capture_output=True)
            self.assertEqual(json.loads(pathlib.Path(home, "hooks.json").read_text()), existing)


if __name__ == "__main__":
    unittest.main()
