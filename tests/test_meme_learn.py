"""2.3.0「分享后自动学梗」的单元测试。

覆盖：
- SeenStore 的学梗记录（memes / meme_daily）：判过就是判过、跨天、跨重启
- _meme_block_reason 的每一条拦截理由
- 分享成功后排后台任务、开关关掉/额度用完/同一条视频不重复
- _auto_learn_meme 的成功、判为非梗、抛异常、读视频失败
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import FakeCtx, load_plugin_module, load_submodule, make_video  # noqa: E402
from test_plugin import base_config  # noqa: E402


MEME_ON = {"enabled": True, "auto_learn": True, "auto_from_share": True, "auto_max_per_day": 3}


class MemeStoreTests(unittest.TestCase):
    """学梗记录：判过就算判过（哪怕是「不是梗」），要能跨重启。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store_module = load_submodule("store")
        self.path = Path(self.tmp.name) / "state.json"

    def _store(self):
        store = self.store_module.SeenStore(self.path)
        store.load()
        return store

    def test_mark_and_seen(self):
        store = self._store()
        self.assertFalse(store.meme_seen("BV1"))
        store.meme_mark("BV1", content="yyds", ok=True)
        self.assertTrue(store.meme_seen("BV1"))

    def test_not_a_meme_is_also_remembered(self):
        store = self._store()
        store.meme_mark("BV1", ok=False)
        self.assertTrue(store.meme_seen("BV1"))
        summary = store.meme_summary()
        self.assertEqual(summary["learned_total"], 1)
        self.assertEqual(summary["learned_ok"], 0)

    def test_records_survive_save_and_load(self):
        store = self._store()
        store.meme_mark("BV1", content="yyds", ok=True)
        store.meme_note_today()
        store.save()
        reloaded = self._store()
        self.assertTrue(reloaded.meme_seen("BV1"))
        self.assertEqual(reloaded.memes_today(), 1)

    def test_save_writes_meme_keys(self):
        store = self._store()
        store.meme_mark("BV1", content="yyds", ok=True)
        store.meme_note_today()
        store.save()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("memes", payload)
        self.assertIn("meme_daily", payload)
        self.assertIn("BV1", payload["memes"])

    def test_today_counter_is_per_day(self):
        store = self._store()
        day1 = 1_760_000_000.0
        store.meme_note_today(now=day1)
        self.assertEqual(store.memes_today(now=day1), 1)
        self.assertEqual(store.memes_today(now=day1 + 86400.0), 0)

    def test_remaining_clamps_at_zero(self):
        store = self._store()
        self.assertEqual(store.meme_remaining(2), 2)
        store.meme_note_today()
        store.meme_note_today()
        self.assertEqual(store.meme_remaining(2), 0)
        self.assertGreater(store.meme_remaining(0), 1000)

    def test_old_records_are_pruned(self):
        store = self._store()
        now = time.time()
        store.meme_mark("BV_OLD", content="old", ok=True, now=now - 400 * 86400.0)
        store.meme_mark("BV_NEW", content="new", ok=True, now=now)
        store.save()
        self.assertFalse(store.meme_seen("BV_OLD"))
        self.assertTrue(store.meme_seen("BV_NEW"))


class MemePluginTestCase(unittest.IsolatedAsyncioTestCase):
    """公共脚手架：一份能分享的插件 + 一个白名单会话。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.module = load_plugin_module()
        self.video_module = load_submodule("bilibili")
        self.bili_read = load_submodule("bili_read")
        self.ctx = FakeCtx(self.tmp_dir)

    def _plugin(self, **overrides):
        plugin = self.module.BilibiliInterestSharePlugin()
        plugin._set_context(self.ctx)
        plugin.set_plugin_config(base_config(**overrides))
        plugin._data_dir = self.tmp_dir
        plugin._store = self.module.SeenStore(self.tmp_dir / "state.json")
        plugin._store.load()
        return plugin

    def _meme_plugin(self, **meme_overrides):
        meme = dict(MEME_ON)
        meme.update(meme_overrides)
        return self._plugin(meme=meme)

    def _target(self, **kwargs):
        data = {
            "name": "测试群",
            "enabled": True,
            "stream_id": "qq:GroupMessage:1",
            "group_id": "1083138200",
            "user_id": "",
            "interests": ["原神"],
            "categories": [],
            "max_share_per_round": 1,
            "max_share_per_day": 0,
        }
        data.update(kwargs)
        return SimpleNamespace(**data)

    def _profile(self):
        profile = self.module.InterestProfile()
        profile.add("原神", 1.0)
        return profile

    def _brief(self, **overrides):
        data = {
            "bvid": "BV1TEST00001",
            "title": "原神新版本测评",
            "owner": "测试UP",
            "description": "本期聊聊原神新版本",
            "comments": [("观众A", "笑死", 12)],
            "danmaku": ["哈哈哈哈"],
        }
        data.update(overrides)
        return self.bili_read.VideoBrief(**data)


class MemeBlockReasonTests(MemePluginTestCase):
    """拦截理由要能说清「为什么不学」，方便用户在日志里定位。"""

    async def test_disabled_blocks(self):
        plugin = self._plugin(meme={"enabled": False})
        self.assertIn("启用学梗", plugin._meme_block_reason("BV1"))

    async def test_auto_learn_off_blocks(self):
        plugin = self._meme_plugin(auto_learn=False)
        self.assertIn("自动学梗", plugin._meme_block_reason("BV1"))

    async def test_daily_cap_blocks(self):
        plugin = self._meme_plugin(auto_max_per_day=2)
        plugin._store.meme_note_today()
        plugin._store.meme_note_today()
        self.assertIn("上限", plugin._meme_block_reason("BV1"))

    async def test_already_judged_blocks(self):
        plugin = self._meme_plugin()
        plugin._store.meme_mark("BV1", ok=False)
        self.assertIn("以前已经学过", plugin._meme_block_reason("BV1"))

    async def test_learned_this_run_blocks(self):
        plugin = self._meme_plugin()
        plugin._meme_learned.add("BV1")
        self.assertIn("这次运行", plugin._meme_block_reason("BV1"))

    async def test_empty_reason_when_allowed(self):
        plugin = self._meme_plugin()
        self.assertEqual(plugin._meme_block_reason("BV1"), "")

    async def test_should_learn_meme_needs_title_hint(self):
        plugin = self._meme_plugin()
        self.assertFalse(plugin._should_learn_meme(self._brief(title="原神新版本测评")))
        self.assertTrue(plugin._should_learn_meme(self._brief(title="这个梗指南讲得太清楚了")))

    async def test_auto_max_per_day_zero_means_unlimited(self):
        plugin = self._meme_plugin(auto_max_per_day=0)
        for _ in range(20):
            plugin._store.meme_note_today()
        self.assertEqual(plugin._meme_block_reason("BV1"), "")


class ShareLearnTriggerTests(MemePluginTestCase):
    """分享成功的视频会排一个后台任务去学梗。"""

    async def _share_and_drain(self, plugin, video=None):
        calls = []

        async def fake_learn(v, stream_id):
            calls.append((getattr(v, "bvid", ""), stream_id))

        plugin._auto_learn_video = fake_learn  # type: ignore[assignment]
        detail = await plugin._process_target(
            self._target(),
            "qq:GroupMessage:1",
            self._profile(),
            [video or make_video(self.video_module)],
            {},
        )
        self.assertEqual(detail["shared"], 1)
        tasks = [task for task in list(plugin._bg_tasks) if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return calls

    async def test_share_schedules_background_task(self):
        plugin = self._meme_plugin()
        calls = await self._share_and_drain(plugin)
        self.assertEqual(calls, [("BV1TEST00001", "qq:GroupMessage:1")])

    async def test_auto_from_share_off_skips(self):
        plugin = self._meme_plugin(auto_from_share=False)
        calls = await self._share_and_drain(plugin)
        self.assertEqual(calls, [])

    async def test_meme_disabled_skips(self):
        plugin = self._plugin(meme={"enabled": False})
        calls = await self._share_and_drain(plugin)
        self.assertEqual(calls, [])

    async def test_daily_cap_skips(self):
        plugin = self._meme_plugin(auto_max_per_day=1)
        plugin._store.meme_note_today()
        calls = await self._share_and_drain(plugin)
        self.assertEqual(calls, [])

    async def test_same_bvid_is_scheduled_once(self):
        plugin = self._meme_plugin()
        video = make_video(self.video_module)
        calls = await self._share_and_drain(plugin, video)
        plugin._meme_learned.add("BV1TEST00001")
        plugin._schedule_meme_learn(video, "qq:GroupMessage:1")
        tasks = [task for task in list(plugin._bg_tasks) if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.assertEqual(len(calls), 1)

    async def test_empty_stream_id_skips(self):
        plugin = self._meme_plugin()
        calls = []

        async def fake_learn(v, stream_id):
            calls.append(stream_id)

        plugin._auto_learn_video = fake_learn  # type: ignore[assignment]
        plugin._schedule_meme_learn(make_video(self.video_module), "")
        self.assertEqual(calls, [])


class AutoLearnMemeTests(MemePluginTestCase):
    """_auto_learn_meme：成功记梗、判为非梗也记、异常可重试。"""

    def _plugin_with_learn(self, result, *, note_calls=None):
        plugin = self._meme_plugin()

        async def fake_learn(brief, *, hint=""):
            if isinstance(result, Exception):
                raise result
            return result

        async def fake_note(stream_id, text, *, visible=""):
            if note_calls is not None:
                note_calls.append({"stream_id": stream_id, "text": text, "visible": visible})
            return True

        plugin._learn_from_brief = fake_learn  # type: ignore[assignment]
        plugin._append_note = fake_note  # type: ignore[assignment]
        return plugin

    async def test_success_marks_and_notes(self):
        notes = []
        plugin = self._plugin_with_learn((True, "yyds：永远的神\n解释…"), note_calls=notes)
        await plugin._auto_learn_meme(self._brief(), "qq:GroupMessage:1")
        self.assertTrue(plugin._store.meme_seen("BV1TEST00001"))
        self.assertEqual(plugin._store.memes_today(), 1)
        summary = plugin._store.meme_summary()
        self.assertEqual(summary["learned_ok"], 1)
        self.assertEqual(len(notes), 1)
        self.assertIn("yyds", notes[0]["text"])
        self.assertEqual(notes[0]["visible"], "学到一个梗")

    async def test_not_a_meme_marks_without_note(self):
        notes = []
        plugin = self._plugin_with_learn((False, "这段内容不是梗"), note_calls=notes)
        await plugin._auto_learn_meme(self._brief(), "qq:GroupMessage:1")
        self.assertTrue(plugin._store.meme_seen("BV1TEST00001"))
        self.assertEqual(plugin._store.meme_summary()["learned_ok"], 0)
        self.assertEqual(notes, [])

    async def test_exception_keeps_bvid_retryable(self):
        plugin = self._plugin_with_learn(RuntimeError("模型超时"))
        await plugin._auto_learn_meme(self._brief(), "qq:GroupMessage:1")
        self.assertNotIn("BV1TEST00001", plugin._meme_learned)
        self.assertFalse(plugin._store.meme_seen("BV1TEST00001"))

    async def test_read_failure_discards_bvid(self):
        plugin = self._meme_plugin()

        async def boom(raw, **kwargs):
            raise RuntimeError("读视频失败")

        plugin.read_video = boom  # type: ignore[assignment]
        plugin._meme_learned.add("BV1TEST00001")
        await plugin._auto_learn_video(make_video(self.video_module), "qq:GroupMessage:1")
        self.assertNotIn("BV1TEST00001", plugin._meme_learned)

    async def test_read_success_hands_brief_to_learner(self):
        brief = self._brief()
        seen = []

        async def fake_read(raw, **kwargs):
            return brief, "正文"

        async def fake_learn(b, stream_id):
            seen.append((getattr(b, "bvid", ""), stream_id))

        plugin = self._meme_plugin()
        plugin.read_video = fake_read  # type: ignore[assignment]
        plugin._auto_learn_meme = fake_learn  # type: ignore[assignment]
        await plugin._auto_learn_video(make_video(self.video_module), "qq:GroupMessage:1")
        self.assertEqual(seen, [("BV1TEST00001", "qq:GroupMessage:1")])


if __name__ == "__main__":
    unittest.main()
