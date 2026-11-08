"""分享形式（文字 / 封面图文）与多段活跃时间的单元测试。"""

from __future__ import annotations

import asyncio
import base64
import inspect
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx

from _helpers import FakeCtx, load_plugin_module, load_submodule, make_video  # noqa: E402
from test_plugin import base_config  # noqa: E402


class ActiveHoursTests(unittest.TestCase):
    """活跃时间：多段、午休空档、跨午夜、写错时的兜底。"""

    def setUp(self) -> None:
        self.module = load_plugin_module()

    def test_single_window_keeps_working(self):
        windows = self.module._parse_active_windows("08:00-21:00")
        self.assertEqual(windows, [(480, 1260)])
        self.assertEqual(self.module._parse_active_hours("08:00-21:00"), (480, 1260))

    def test_multi_window_separators(self):
        expected = [(480, 690), (780, 1260)]
        for value in (
            "08:00-11:30, 13:00-21:00",
            "08:00-11:30，13:00-21:00",
            "08:00-11:30;13:00-21:00",
            "08:00-11:30；13:00-21:00",
            "08:00-11:30、13:00-21:00",
            "08:00~11:30, 13:00-21:00",
        ):
            self.assertEqual(self.module._parse_active_windows(value), expected, value)

    def test_lunch_break_is_skipped(self):
        value = "08:00-11:30, 13:00-21:00"
        self.assertTrue(self.module._in_active_window(value, now=datetime(2026, 11, 8, 9, 0)))
        self.assertFalse(
            self.module._in_active_window(value, now=datetime(2026, 11, 8, 12, 0))
        )
        self.assertTrue(self.module._in_active_window(value, now=datetime(2026, 11, 8, 13, 0)))
        self.assertFalse(
            self.module._in_active_window(value, now=datetime(2026, 11, 8, 21, 0))
        )

    def test_window_end_is_exclusive(self):
        value = "08:00-11:30"
        self.assertTrue(self.module._in_active_window(value, now=datetime(2026, 11, 8, 11, 29)))
        self.assertFalse(self.module._in_active_window(value, now=datetime(2026, 11, 8, 11, 30)))

    def test_midnight_window(self):
        value = "22:00-02:00"
        self.assertTrue(self.module._in_active_window(value, now=datetime(2026, 11, 8, 23, 30)))
        self.assertTrue(self.module._in_active_window(value, now=datetime(2026, 11, 8, 1, 0)))
        self.assertFalse(self.module._in_active_window(value, now=datetime(2026, 11, 8, 12, 0)))

    def test_midnight_window_in_multi(self):
        value = "08:00-11:30, 22:00-02:00"
        self.assertTrue(self.module._in_active_window(value, now=datetime(2026, 11, 8, 1, 0)))
        self.assertFalse(self.module._in_active_window(value, now=datetime(2026, 11, 8, 15, 0)))

    def test_blank_means_all_day(self):
        self.assertIsNone(self.module._parse_active_windows(""))
        self.assertIsNone(self.module._parse_active_windows("   "))
        self.assertTrue(self.module._in_active_window("", now=datetime(2026, 11, 8, 3, 0)))

    def test_typo_falls_back_to_all_day(self):
        # 一段写错就按全天：宁可多发，也不要因为一个笔误完全不分享
        self.assertIsNone(self.module._parse_active_windows("08:00-11:30, abc"))
        self.assertTrue(
            self.module._in_active_window("08:00-11:30, abc", now=datetime(2026, 11, 8, 12, 0))
        )

    def test_same_start_end_means_all_day(self):
        self.assertTrue(
            self.module._in_active_window("09:00-09:00", now=datetime(2026, 11, 8, 15, 0))
        )


class ShareStyleDispatchTests(unittest.IsolatedAsyncioTestCase):
    """_send_parts 按 share.style 分派，并逐级回退。"""

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
        return plugin

    def _cover(self, plugin, data=b"cover-bytes"):
        async def fake_download(url: str):
            return data

        plugin._download_cover = fake_download

    async def test_text_style_is_default(self):
        plugin = self._plugin()
        video = make_video(self.video_module)
        ok = await plugin._send_parts("stream-1", video, "这也太好玩了")
        self.assertTrue(ok)
        self.assertEqual(len(self.ctx.send.messages), 1)
        text = self.ctx.send.messages[0]["text"]
        self.assertIn("这也太好玩了", text)
        self.assertIn("【原神新版本测评】", text)
        self.assertIn("https://www.bilibili.com/video/BV1TEST00001", text)
        self.assertEqual(self.ctx.send.images, [])

    async def test_text_style_with_stats_line(self):
        plugin = self._plugin(share={"include_stats_line": True})
        video = make_video(self.video_module)
        await plugin._send_parts("stream-1", video, "看看")
        self.assertIn("播放", self.ctx.send.messages[0]["text"])

    async def test_image_style_sends_one_mixed_message(self):
        plugin = self._plugin(share={"style": "image"})
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        ok = await plugin._send_parts("stream-1", video, "看看这个")
        self.assertTrue(ok)
        # 封面图和文字合成一条发出去，不再是两条独立消息
        self.assertEqual(len(self.ctx.send.hybrids), 1)
        self.assertEqual(self.ctx.send.images, [])
        segments = self.ctx.send.hybrids[0]["segments"]
        self.assertEqual([item["type"] for item in segments], ["image", "text"])
        self.assertEqual(base64.b64decode(segments[0]["content"]), b"cover-bytes")
        # 第一条只有标题链接，随口一句不掺进去
        self.assertIn("【原神新版本测评】", segments[1]["content"])
        self.assertIn("share_source=copy_web", segments[1]["content"])
        self.assertNotIn("看看这个", segments[1]["content"])
        # 随口一句单独一条，排在封面 + 链接之后
        self.assertEqual([item["text"] for item in self.ctx.send.messages], ["看看这个"])

    async def test_image_style_can_still_send_two_messages(self):
        plugin = self._plugin(share={"style": "image", "single_message": False})
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        ok = await plugin._send_parts("stream-1", video, "看看这个")
        self.assertTrue(ok)
        self.assertEqual(self.ctx.send.hybrids, [])
        self.assertEqual(len(self.ctx.send.images), 1)
        self.assertIn("【原神新版本测评】", self.ctx.send.messages[0]["text"])

    async def test_image_style_falls_back_when_hybrid_unsupported(self):
        plugin = self._plugin(share={"style": "image"})
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        self.ctx.send.hybrid = None  # 老宿主没有 send.hybrid
        ok = await plugin._send_parts("stream-1", video, "看看这个")
        self.assertTrue(ok)
        self.assertEqual(len(self.ctx.send.images), 1)
        self.assertIn("【原神新版本测评】", self.ctx.send.messages[0]["text"])

    async def test_image_style_falls_back_when_hybrid_fails(self):
        plugin = self._plugin(share={"style": "image"})
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)

        async def failing_hybrid(segments, stream_id, **kwargs):
            return {"success": False, "error": "未命中任何发送路由"}

        self.ctx.send.hybrid = failing_hybrid
        ok = await plugin._send_parts("stream-1", video, "看看这个")
        self.assertTrue(ok)
        self.assertEqual(len(self.ctx.send.images), 1)

    async def test_image_style_single_message_without_cover_sends_text(self):
        plugin = self._plugin(share={"style": "image"})
        video = make_video(self.video_module)
        self._cover(plugin, None)
        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(self.ctx.send.hybrids, [])
        self.assertIn("【原神新版本测评】", self.ctx.send.messages[0]["text"])

    async def test_image_style_falls_back_without_cover(self):
        plugin = self._plugin(share={"style": "image"})
        video = make_video(self.video_module)  # 没有封面
        self._cover(plugin, None)
        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(self.ctx.send.images, [])
        self.assertIn("【原神新版本测评】", self.ctx.send.messages[0]["text"])

    def test_roll_casual_boundaries(self):
        plugin = self._plugin(share={"casual_chance": 0})
        self.assertFalse(plugin._roll_casual())
        plugin = self._plugin(share={"casual_chance": 100})
        self.assertTrue(plugin._roll_casual())

    def test_roll_casual_uses_random(self):
        plugin = self._plugin(share={"casual_chance": 50})
        with patch.object(self.module.random, "random", return_value=0.2):
            self.assertTrue(plugin._roll_casual())
        with patch.object(self.module.random, "random", return_value=0.8):
            self.assertFalse(plugin._roll_casual())

    async def test_casual_chance_zero_skips_narrative(self):
        plugin = self._plugin(share={"style": "image", "casual_chance": 0})
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        ok = await plugin._send_parts("stream-1", video, "看看这个")
        self.assertTrue(ok)
        self.assertEqual(len(self.ctx.send.hybrids), 1)
        # 只发封面 + 链接，那句随口话一次都不发
        self.assertEqual(self.ctx.send.messages, [])

    async def test_casual_chance_zero_text_style(self):
        plugin = self._plugin(share={"casual_chance": 0})
        video = make_video(self.video_module)
        await plugin._send_parts("stream-1", video, "这也太好玩了")
        self.assertEqual(len(self.ctx.send.messages), 1)
        text = self.ctx.send.messages[0]["text"]
        self.assertIn("【原神新版本测评】", text)
        self.assertNotIn("这也太好玩了", text)

    async def test_casual_chance_partial_keeps_or_drops(self):
        video = make_video(self.video_module)
        plugin = self._plugin(share={"casual_chance": 50})
        with patch.object(self.module.random, "random", return_value=0.2):
            await plugin._send_parts("stream-1", video, "看看这个")
        self.assertIn("看看这个", self.ctx.send.messages[0]["text"])
        self.ctx.send.messages.clear()
        plugin = self._plugin(share={"casual_chance": 50})
        with patch.object(self.module.random, "random", return_value=0.9):
            await plugin._send_parts("stream-1", video, "看看这个")
        self.assertNotIn("看看这个", self.ctx.send.messages[0]["text"])


class ShareLinkTests(unittest.TestCase):
    """分享链接用 B 站官方「复制链接」格式。"""

    def setUp(self) -> None:
        self.module = load_plugin_module()
        self.video_module = load_submodule("bilibili")

    def test_builds_official_link(self):
        link = self.module.build_share_link(
            "https://www.bilibili.com/video/BV1VDWJ6VEXs", "55d6a24d42e550c9ccab2084f87c67d0"
        )
        self.assertEqual(
            link,
            "https://www.bilibili.com/video/BV1VDWJ6VEXs/?share_source=copy_web"
            "&vd_source=55d6a24d42e550c9ccab2084f87c67d0",
        )

    def test_without_vd_source(self):
        link = self.module.build_share_link("https://www.bilibili.com/video/BV1oGxs6hEWP/")
        self.assertEqual(
            link, "https://www.bilibili.com/video/BV1oGxs6hEWP/?share_source=copy_web"
        )

    def test_keeps_existing_query(self):
        link = self.module.build_share_link(
            "https://www.bilibili.com/video/BV1Nn4w6QEJ1/?p=2&t=30", ""
        )
        self.assertEqual(
            link,
            "https://www.bilibili.com/video/BV1Nn4w6QEJ1/?p=2&t=30&share_source=copy_web",
        )

    def test_empty_url_returns_empty(self):
        self.assertEqual(self.module.build_share_link(""), "")

    def test_header_uses_official_link(self):
        video = make_video(self.video_module)  # url 由 bvid 推导：BV1TEST00001
        header = self.module.format_share_header(video, "abc123")
        self.assertTrue(header.startswith("【"))
        self.assertIn("https://www.bilibili.com/video/BV1TEST00001/?share_source=copy_web", header)
        self.assertIn("vd_source=abc123", header)
