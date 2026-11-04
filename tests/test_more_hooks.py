#!/usr/bin/env python3
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "consent_hooks"))
import autocommit_agents_md  # noqa: E402
import common  # noqa: E402
import language_check  # noqa: E402
import schedule_check  # noqa: E402
import sidebar_sections  # noqa: E402

EXAMPLE = json.loads((REPO / "config.example.json").read_text(encoding="utf-8"))


class TempHome(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.saved = {k: os.environ.get(k) for k in ("CODEX_HOME", common.CONFIG_ENV)}
        os.environ["CODEX_HOME"] = self.temp.name
        self.config_path = pathlib.Path(self.temp.name) / "consent-hooks.json"
        os.environ[common.CONFIG_ENV] = str(self.config_path)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp.cleanup()

    def write_config(self, data):
        self.config_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class LanguageTests(unittest.TestCase):
    rules = EXAMPLE["language_check"]["rules"]

    def check(self, text, evaluator=None):
        return language_check.find_violations(text, self.rules, ["依頼"], evaluator)

    def test_listed_phrases_are_found_without_repeating_them(self):
        found = self.check("作業は終わりました。すみません、表は次のとおりです。")
        self.assertEqual(len(found), 1)
        self.assertNotIn("すみません", found[0])
        self.assertIn("文字目", found[0])

    def test_clean_reply_passes(self):
        self.assertEqual(self.check("表を更新し、テストが通ることを確認しました。"), [])

    def test_contextual_rule_is_read_by_the_judge(self):
        text = "先に確認するべきでした。外すべきでしたら外します。"
        judged = self.check(text, lambda p, s: {"violations": ["先に確認するべきでした。"], "reason": ""})
        self.assertEqual(len(judged), 1)
        self.assertEqual(self.check(text, lambda p, s: {"violations": [], "reason": ""}), [])

        def broken(prompt, schema):
            raise common.JudgeError("timeout")
        self.assertEqual(len(self.check(text, broken)), 2)

    def test_quoted_phrase_is_not_counted_for_skip_quoted_rules(self):
        self.assertEqual(self.check("「確認するべきでした」という表現は使いません。", lambda p, s: {"violations": [], "reason": ""}), [])


class ScheduleTests(TempHome):
    def make_db(self):
        (pathlib.Path(self.temp.name) / "sqlite").mkdir()
        db = sqlite3.connect(str(schedule_check.database()))
        db.execute("CREATE TABLE automations (id TEXT, name TEXT, status TEXT, rrule TEXT, next_run_at INTEGER, updated_at INTEGER)")
        db.commit()
        return db

    def test_first_run_is_silent_and_changes_are_reported_in_the_time_zone(self):
        db = self.make_db()
        config = common._merge(common.DEFAULT_CONFIG, {"schedule_check": {"timezone": "Asia/Tokyo"}})
        self.assertEqual(schedule_check.decide(config, False), {})
        fire = int(time.time() * 1000) + 3_600_000
        db.execute("INSERT INTO automations VALUES ('a1', '朝の確認', 'ACTIVE', 'FREQ=DAILY;BYHOUR=0', ?, ?)", (fire, 5))
        db.commit()
        out = schedule_check.decide(config, False)
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("朝の確認", text)
        self.assertIn("Asia/Tokyo", text)
        self.assertEqual(schedule_check.decide(config, True), {})  # the same change is not repeated
        db.execute("INSERT INTO automations VALUES ('a2', '夜', 'ACTIVE', 'FREQ=DAILY', NULL, 9)")
        db.commit()
        self.assertEqual(schedule_check.decide(config, True)["decision"], "block")


class AutocommitTests(TempHome):
    def test_only_the_tracked_file_is_committed(self):
        repo = pathlib.Path(self.temp.name) / "repo"
        repo.mkdir()
        run = lambda *a: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
        run("init", "-q")
        run("config", "user.email", "test@example.invalid")
        run("config", "user.name", "test")
        (repo / "AGENTS.md").write_text("a\n")
        (repo / "other.txt").write_text("x\n")
        run("add", "AGENTS.md")
        run("commit", "-q", "-m", "init")
        target = {"path": str(repo), "file": "AGENTS.md", "message": "auto"}
        self.assertEqual(autocommit_agents_md.commit(target), "")
        (repo / "AGENTS.md").write_text("b\n")
        self.assertIn("auto", autocommit_agents_md.commit(target))
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "?? other.txt")
        self.assertEqual(autocommit_agents_md.commit({"path": str(repo), "file": "missing.md"}), "skip: file not tracked")


class SidebarTests(TempHome):
    def make_state(self):
        db = sqlite3.connect(str(pathlib.Path(self.temp.name) / "state_5.sqlite"))
        db.execute("CREATE TABLE thread_sections (id TEXT, name TEXT)")
        db.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT, archived INTEGER, is_pinned INTEGER, thread_section_id TEXT)")
        db.executemany("INSERT INTO thread_sections VALUES (?, ?)",
                       [("s-pin", "Pinned"), ("s-on", "進行中"), ("s-wait", "待ち"), ("s-shelf", "棚上げ")])
        db.execute("INSERT INTO threads VALUES ('t1', '', 0, 0, NULL)")
        db.commit()

    def test_commands_parse_to_section_names(self):
        self.assertEqual(sidebar_sections.parse_pin_command("/p1"), "進行中")
        self.assertEqual(sidebar_sections.parse_pin_command("／ｐ４".replace("ｐ", "p")), "完了")
        self.assertEqual(sidebar_sections.parse_pin_command("/p"), "外す")
        self.assertEqual(sidebar_sections.parse_pin_command("/pinn 棚上げ"), "棚上げ")
        self.assertIsNone(sidebar_sections.parse_pin_command("/pin"))
        self.assertIsNone(sidebar_sections.parse_pin_command("p1 を見て"))

    def test_section_ids_come_from_names_unless_configured(self):
        self.make_state()
        self.assertEqual(sidebar_sections.settings()["section_ids"], {"ongoing": "s-on", "waiting": "s-wait"})
        self.write_config({"sidebar": {"section_ids": {"ongoing": "x", "waiting": "y"}}})
        self.assertEqual(sidebar_sections.settings()["section_ids"], {"ongoing": "x", "waiting": "y"})

    def test_unknown_section_is_answered_without_moving(self):
        self.make_state()
        out = sidebar_sections.handle_pin_command("t1", {}, "存在しない")
        self.assertEqual(out["decision"], "block")
        self.assertIn("棚上げ", out["reason"])
        self.assertEqual(sidebar_sections.read_manual_moves(), {})

    def test_hand_moves_and_pins_take_precedence(self):
        self.make_state()
        self.assertTrue(sidebar_sections.automatic_move_allowed("t1"))
        db = sqlite3.connect(str(pathlib.Path(self.temp.name) / "state_5.sqlite"))
        db.execute("UPDATE threads SET thread_section_id='s-shelf'")
        db.commit()
        self.assertFalse(sidebar_sections.automatic_move_allowed("t1"))
        self.assertTrue(sidebar_sections.resumed_from_shelf("t1"))
        sidebar_sections.record_manual_move("t1", "turn", "s-shelf")
        self.assertFalse(sidebar_sections.resumed_from_shelf("t1"))
        db.execute("UPDATE threads SET thread_section_id=NULL, is_pinned=1")
        db.commit()
        self.assertFalse(sidebar_sections.automatic_move_allowed("t1"))


if __name__ == "__main__":
    unittest.main()
