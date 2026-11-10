"""每日分享上限与「重复分享」加固的单元测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import FakeCtx, load_plugin_module, load_submodule, make_video  # noqa: E402
from test_plugin import base_config  # noqa: E402


class DailyCounterTests(unittest.TestCase):
    """SeenStore 的每日计数：按会话 / 按天分开，存盘能读回。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_module = load_submodule("store")
        self.path = Path(self.tmp.name) / "state.json"

    def _store(self) -> "object":
        store = self.store_module.SeenStore(self.path)
        store.load()
        return store

    def test_counts_per_stream_and_total(self):
        store = self._store()
        self.assertEqual(store.shares_today("qq:GroupMessage:1"), 0)
        self.assertEqual(store.note_shared_today("qq:GroupMessage:1"), 1)
        self.assertEqual(store.note_shared_today("qq:GroupMessage:1"), 2)
        self.assertEqual(store.shares_today("qq:GroupMessage:1"), 2)
        self.assertEqual(store.shares_today("qq:GroupMessage:2"), 0)
        self.assertEqual(store.total_today(), 2)

    def test_counts_are_per_day(self):
        store = self._store()
        day1 = 1_760_000_000.0
        store.note_shared_today("s1", now=day1)
        self.assertEqual(store.shares_today("s1", now=day1), 1)
        self.assertEqual(store.shares_today("s1", now=day1 + 86400.0), 0)

    def test_daily_remaining_clamps_at_zero(self):
        store = self._store()
        self.assertEqual(store.daily_remaining("s1", 3), 3)
        store.note_shared_today("s1")
        store.note_shared_today("s1")
        store.note_shared_today("s1")
        self.assertEqual(store.daily_remaining("s1", 3), 0)
        # limit = 0 表示不限制
        self.assertGreater(store.daily_remaining("s1", 0), 1000)

    def test_counts_survive_save_and_load(self):
        store = self._store()
        store.note_shared_today("s1")
        store.note_shared_today("s1")
        store.save()
        reloaded = self._store()
        self.assertEqual(reloaded.shares_today("s1"), 2)
        self.assertEqual(reloaded.total_today(), 2)

    def test_save_keeps_state_file_shape(self):
        store = self._store()
        store.note_shared_today("s1")
        store.save()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("daily", payload)
        self.assertEqual(sum(payload["daily"].values().__iter__().__next__().values()), 2)

    def test_old_days_are_pruned(self):
        store = self._store()
        now = time.time()
        store.note_shared_today("s1", now=now)
        store.note_shared_today("s1", now=now - 30 * 86400.0)
        store.save()
        self.assertEqual(store.shares_today("s1", now=now), 1)


class SendResultTests(unittest.TestCase):
    """发送回执判定：只要宿主说成功了就不算失败，避免重复分享。"""

    def setUp(self) -> None:
        self.module = load_plugin_module()

    def test_sent_false_but_success_true_counts_as_sent(self):
        ok = self.module.BilibiliInterestSharePlugin._send_result_ok(
            {"success": True, "sent": False}, "s1"
        )
        self.assertTrue(ok)

    def test_success_false_is_failure(self):
        ok = self.module.BilibiliInterestSharePlugin._send_result_ok(
            {"success": False, "sent": False}, "s1"
        )
        self.assertFalse(ok)

    def test_sent_true_is_success(self):
        ok = self.module.BilibiliInterestSharePlugin._send_result_ok(
            {"sent": True}, "s1"
        )
        self.assertTrue(ok)


class DailyLimitTests(unittest.IsolatedAsyncioTestCase):
    """自动分享轮到每日上限就跳过。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.module = load_plugin_module()
        self.video_module = load_submodule("bilibili")
        self.ctx = FakeCtx(self.tmp_dir)

    def _plugin(self, **overrides):
        plugin = self.module.BilibiliInterestSharePlugin()
        plugin._set_context(self.ctx)
        plugin.set_plugin_config(base_config(**overrides))
        plugin._data_dir = self.tmp_dir
        plugin._store = self.module.SeenStore(self.tmp_dir / "state.json")
        plugin._store.load()
        return plugin

    def _target(self, **kwargs):
        data = {
            "name": "测试群",
            "enabled": True,
            "stream_id": "qq:GroupMessage:1",
            "group_id": "1083138200",
            "user_id": "",
            "categories": [],
            "max_share_per_round": 1,
            "max_share_per_day": 0,
        }
        data.update(kwargs)
        return SimpleNamespace(**data)

    async def test_round_is_skipped_when_daily_cap_reached(self):
        plugin = self._plugin(schedule={"max_share_per_day": 2})
        plugin._store.note_shared_today("qq:GroupMessage:1")
        plugin._store.note_shared_today("qq:GroupMessage:1")
        detail = await plugin._process_target(
            self._target(),
            "qq:GroupMessage:1",
            None,
            [make_video(self.video_module)],
            {},
        )
        self.assertEqual(detail["shared"], 0)
        self.assertEqual(detail["skipped"], "daily_limit_reached")
        self.assertEqual(self.ctx.send.messages, [])

    async def test_stream_cap_overrides_schedule(self):
        plugin = self._plugin(schedule={"max_share_per_day": 10})
        target = self._target(max_share_per_day=1)
        plugin._store.note_shared_today("qq:GroupMessage:1")
        detail = await plugin._process_target(
            target,
            "qq:GroupMessage:1",
            None,
            [make_video(self.video_module)],
            {},
        )
        self.assertEqual(detail["skipped"], "daily_limit_reached")

    async def test_zero_limit_means_unlimited(self):
        plugin = self._plugin(schedule={"max_share_per_day": 0})
        for _ in range(20):
            plugin._store.note_shared_today("qq:GroupMessage:1")
        detail = await plugin._process_target(
            self._target(),
            "qq:GroupMessage:1",
            None,
            [],
            {},
        )
        # 视频列表为空所以没分享，但没被每日上限拦掉
        self.assertNotIn("skipped", detail)

    def _profile(self):
        profile = self.module.InterestProfile()
        profile.add("原神", 1.0)
        return profile

    async def test_successful_share_counts_and_saves_immediately(self):
        plugin = self._plugin(schedule={"max_share_per_day": 5})
        detail = await plugin._process_target(
            self._target(),
            "qq:GroupMessage:1",
            self._profile(),
            [make_video(self.video_module)],
            {},
        )
        self.assertEqual(detail["shared"], 1)
        self.assertEqual(plugin._store.shares_today("qq:GroupMessage:1"), 1)
        reloaded = self.module.SeenStore(self.tmp_dir / "state.json")
        reloaded.load()
        self.assertEqual(reloaded.shares_today("qq:GroupMessage:1"), 1)

    def test_daily_limit_for_reads_stream_config(self):
        plugin = self._plugin(schedule={"max_share_per_day": 7})
        plugin.set_plugin_config(
            base_config(schedule={"max_share_per_day": 7}, streams=[self._target(max_share_per_day=3).__dict__])
        )
        self.assertEqual(plugin._daily_limit_for("qq:GroupMessage:1"), 3)
        self.assertEqual(plugin._daily_limit_for("qq:GroupMessage:unknown"), 7)


if __name__ == "__main__":
    unittest.main()
