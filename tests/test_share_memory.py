"""分享后写会话上下文 + 搜索权限拆分 + B 站错误码提示的单元测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import (  # noqa: E402
    FakeClient,
    FakeCtx,
    load_plugin_module,
    load_submodule,
    make_video,
)
from test_plugin import base_config  # noqa: E402


class _FakeAppender:
    def __init__(self) -> None:
        self.calls = []

    async def append(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True}


class RememberShareTests(unittest.IsolatedAsyncioTestCase):
    """分享成功后要把视频信息写进会话上下文，麦麦才聊得起来。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.module = load_plugin_module()
        self.video_module = load_submodule("bilibili")
        self.ctx = FakeCtx(self.tmp_dir)
        self.appender = _FakeAppender()
        self.ctx.maisaka = SimpleNamespace(context=self.appender)

    def _plugin(self, **overrides):
        plugin = self.module.BilibiliInterestSharePlugin()
        plugin._set_context(self.ctx)
        plugin.set_plugin_config(base_config(**overrides))
        plugin._data_dir = self.tmp_dir
        return plugin

    async def test_share_writes_context_note(self):
        plugin = self._plugin()
        video = make_video(self.video_module)
        ok = await plugin._send_parts("stream-1", video, "这波操作有点东西")
        self.assertTrue(ok)
        self.assertEqual(len(self.appender.calls), 1)
        call = self.appender.calls[0]
        self.assertEqual(call["stream_id"], "stream-1")
        text = call["segments"][0]["content"]
        self.assertIn("原神新版本测评", text)
        self.assertIn("bilibili.com/video", text)
        self.assertIn("播放", text)
        self.assertIn("这波操作有点东西", text)

    async def test_failed_send_writes_nothing(self):
        self.ctx.send.fail_streams = {"stream-1"}
        plugin = self._plugin()
        video = make_video(self.video_module)
        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertFalse(ok)
        self.assertEqual(self.appender.calls, [])

    async def test_note_survives_missing_context(self):
        self.ctx.maisaka = None
        plugin = self._plugin()
        video = make_video(self.video_module)
        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(len(self.ctx.send.messages), 1)


class SearchPermissionTests(unittest.IsolatedAsyncioTestCase):
    """「搜索」人人可用，「把视频推进会话」才要白名单。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.module = load_plugin_module()
        self.video_module = load_submodule("bilibili")
        self.ctx = FakeCtx(self.tmp_dir)

    def _plugin(self, **overrides):
        client = FakeClient(
            videos=[], search_results=[make_video(self.video_module, bvid="BVS1")]
        )
        plugin = self.module.BilibiliInterestSharePlugin()
        plugin._set_context(self.ctx)
        plugin.set_plugin_config(base_config(**overrides))
        plugin._data_dir = self.tmp_dir
        plugin._client = client
        plugin._store = self.module.SeenStore(self.tmp_dir / "state.json")
        return plugin

    async def test_search_for_reply_returns_candidates(self):
        plugin = self._plugin()
        found = await plugin.search_for_reply("原神", 3)
        self.assertTrue(found["ok"], found)
        self.assertEqual(len(found["candidates"]), 1)
        item = found["candidates"][0]
        self.assertIn("bilibili.com/video", item["url"])
        self.assertIn("share_source=copy_web", item["url"])
        self.assertEqual(self.ctx.send.messages, [])

    async def test_search_for_reply_rejects_empty_query(self):
        plugin = self._plugin()
        found = await plugin.search_for_reply("   ", 3)
        self.assertFalse(found["ok"])
        self.assertEqual(found["reason"], "empty_query")


class ErrorCodeHintTests(unittest.TestCase):
    """B 站错误码要翻译成人话，别把 code=62002 原样丢给模型。"""

    def setUp(self) -> None:
        self.module = load_submodule("bilibili")

    def test_invisible_video_hint(self):
        with self.assertRaises(self.module.BilibiliError) as ctx:
            self.module.BilibiliClient._check_code({"code": 62002, "message": "啥都木有"})
        text = str(ctx.exception)
        self.assertIn("62002", text)
        self.assertIn("稿件不可见", text)

    def test_unknown_code_keeps_message(self):
        with self.assertRaises(self.module.BilibiliError) as ctx:
            self.module.BilibiliClient._check_code({"code": 12345, "message": "未知错误"})
        self.assertIn("未知错误", str(ctx.exception))

    def test_success_passes_through(self):
        payload = {"code": 0, "data": {"ok": True}}
        self.assertEqual(self.module.BilibiliClient._check_code(payload), payload)


if __name__ == "__main__":
    unittest.main()
