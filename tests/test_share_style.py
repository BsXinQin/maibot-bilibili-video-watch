"""分享形式（文字 / 图文 / 小程序卡片）与多段活跃时间的单元测试。"""

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


def _cover_ready_payload(card):
    return card.build_miniapp_payload(
        title="【测试】原神新版本",
        jump_url="https://www.bilibili.com/video/BV1TEST",
        cover="//i0.hdslb.com/bfs/archive/cover.jpg",
        desc="测试UP",
        pub_time=1700000000,
    )


class ShareCardTests(unittest.TestCase):
    """卡片 JSON 的构造与 OneBot HTTP 请求拼装（不联网）。"""

    def setUp(self) -> None:
        self.card = load_submodule("share_card")

    def test_normalize_cover(self):
        self.assertEqual(
            self.card.normalize_cover("//i0.hdslb.com/a.jpg"),
            "https://i0.hdslb.com/a.jpg",
        )
        self.assertEqual(
            self.card.normalize_cover("http://i0.hdslb.com/a.jpg"),
            "https://i0.hdslb.com/a.jpg",
        )
        self.assertEqual(self.card.normalize_cover("   "), "")
        # 相对路径没法直接引用，当作没有封面（会退回图文）
        self.assertEqual(self.card.normalize_cover("/bfs/a.jpg"), "")

    def test_miniapp_payload(self):
        payload = _cover_ready_payload(self.card)
        self.assertEqual(payload["app"], "com.tencent.miniapp_01")
        self.assertEqual(payload["view"], "viewMultiMsg")
        detail = payload["meta"]["detail_1"]
        self.assertEqual(detail["url"], "https://www.bilibili.com/video/BV1TEST")
        self.assertEqual(detail["preview"], "https://i0.hdslb.com/bfs/archive/cover.jpg")
        self.assertEqual(detail["appid"], self.card.BILIBILI_APPID)
        self.assertEqual(payload["config"]["ctime"], 1700000000)
        self.assertEqual(payload["extra"]["appid"], self.card.BILIBILI_APPID)

    def test_miniapp_requires_cover(self):
        payload = self.card.build_miniapp_payload(
            title="标题",
            jump_url="https://www.bilibili.com/video/BV1TEST",
            cover="",
        )
        self.assertEqual(payload, {})

    def test_build_payload_falls_back_to_news(self):
        # 想要小程序卡片但没封面 → 自动换成通用图文卡片（仍然比纯文字好看）
        payload = self.card.build_payload(
            "miniapp",
            title="标题",
            jump_url="https://www.bilibili.com/video/BV1TEST",
            cover="",
        )
        self.assertEqual(payload["app"], "com.tencent.structmsg")
        self.assertEqual(
            payload["meta"]["news"]["jumpUrl"],
            "https://www.bilibili.com/video/BV1TEST",
        )

    def test_news_first_when_configured(self):
        payload = self.card.build_payload(
            "news",
            title="标题",
            jump_url="https://www.bilibili.com/video/BV1TEST",
            cover="//i0.hdslb.com/c.jpg",
        )
        self.assertEqual(payload["view"], "news")

    def test_post_accepts_use_header_keyword(self):
        # 曾经把 ``*`` 写成单独一行却没带逗号，Python 把它当成了 *use_header，
        # 于是 send_json_card(use_header=True) 一调就 TypeError —— 这里把签名钉住。
        params = inspect.signature(self.card._post).parameters
        self.assertEqual(params["use_header"].kind, inspect.Parameter.KEYWORD_ONLY)

    def test_error_text(self):
        request = httpx.Request("POST", "http://127.0.0.1:3000/send_group_msg")
        response = httpx.Response(403, request=request)
        exc = httpx.HTTPStatusError("boom", request=request, response=response)
        self.assertEqual(self.card.error_text(exc), "HTTP 403")

        class Quiet(Exception):
            def __str__(self) -> str:
                return "   "

        self.assertEqual(self.card.error_text(Quiet()), "Quiet")
        self.assertEqual(self.card.error_text(RuntimeError("连接被拒绝")), "连接被拒绝")

    def test_json_segment_roundtrip(self):
        payload = _cover_ready_payload(self.card)
        segment = self.card.build_json_segment(payload)
        self.assertEqual(segment["type"], "json")
        self.assertEqual(json.loads(segment["data"]["data"]), payload)

    def test_api_url(self):
        self.assertEqual(
            self.card._api_url("http://172.23.0.2:3000", "send_group_msg"),
            "http://172.23.0.2:3000/send_group_msg",
        )
        self.assertEqual(
            self.card._api_url("ws://172.23.0.2:3000", "send_group_msg"),
            "http://172.23.0.2:3000/send_group_msg",
        )
        self.assertEqual(
            self.card._api_url("172.23.0.2:3000/", "send_private_msg"),
            "http://172.23.0.2:3000/send_private_msg",
        )
        self.assertEqual(self.card._api_url("", "x"), "")

    def test_response_ok(self):
        self.assertTrue(self.card.response_ok({"status": "ok", "retcode": 0}))
        self.assertFalse(self.card.response_ok({"status": "failed", "retcode": 100}))
        self.assertFalse(self.card.response_ok({"retcode": 1404, "message": "bad"}))
        self.assertTrue(self.card.response_ok({"code": 0, "data": {}}))

    def test_send_json_card_without_route(self):
        ok, detail = asyncio.run(
            self.card.send_json_card("http://127.0.0.1:3000", "", payload={"x": 1})
        )
        self.assertFalse(ok)
        self.assertIn("群号", detail)

    def test_send_json_card_without_api_base(self):
        ok, detail = asyncio.run(
            self.card.send_json_card("", "", group_id="123", payload={"x": 1})
        )
        self.assertFalse(ok)
        self.assertIn("OneBot", detail)

    def test_coerce_id(self):
        self.assertEqual(self.card._coerce_id("1083138200"), 1083138200)
        self.assertEqual(self.card._coerce_id(" 42 "), 42)
        self.assertEqual(self.card._coerce_id("group-a"), "group-a")
        self.assertEqual(self.card._coerce_id(""), "")
        self.assertEqual(self.card._coerce_id(None), "")

    def test_looks_unsupported(self):
        self.assertTrue(self.card._looks_unsupported({"retcode": 1404, "message": "x"}))
        self.assertTrue(self.card._looks_unsupported({"retcode": 404, "message": "x"}))
        self.assertTrue(
            self.card._looks_unsupported({"status": "failed", "message": "unknown action send_group_msg"})
        )
        self.assertTrue(
            self.card._looks_unsupported({"status": "failed", "wording": "不支持的动作 send_group_msg"})
        )
        self.assertFalse(
            self.card._looks_unsupported({"status": "failed", "retcode": 100, "message": "风控"})
        )
        self.assertFalse(self.card._looks_unsupported({"status": "ok"}))
        self.assertFalse(self.card._looks_unsupported("not a dict"))

    def test_snowluma_falls_back_to_send_msg(self):
        # SnowLuma 画像只实现通用 send_msg：send_group_msg 回 1404 时应自动改发。
        calls = []

        async def fake_post(url, body, token, timeout, *, use_header=True):
            calls.append((url, body, use_header))
            if url.endswith("/send_group_msg"):
                return {"status": "failed", "retcode": 1404, "message": "unknown action"}
            return {"status": "ok", "retcode": 0, "data": {"message_id": 7}}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        ok, detail = asyncio.run(
            self.card.send_json_card(
                "http://172.23.0.2:3000",
                "tok",
                group_id="1083138200",
                payload=_cover_ready_payload(self.card),
                timeout=1.0,
            )
        )

        self.assertTrue(ok, detail)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][0].endswith("/send_group_msg"))
        self.assertEqual(calls[1][0], "http://172.23.0.2:3000/send_msg")
        self.assertEqual(calls[1][1]["message_type"], "group")
        self.assertEqual(calls[1][1]["group_id"], 1083138200)
        self.assertEqual(calls[1][1]["message"][0]["type"], "json")
        self.assertTrue(all(call[2] is True for call in calls))

    def test_private_card_falls_back_to_send_msg(self):
        calls = []

        async def fake_post(url, body, token, timeout, *, use_header=True):
            calls.append((url, body))
            if url.endswith("/send_private_msg"):
                return {"status": "failed", "retcode": 1404, "message": "unknown action"}
            return {"status": "ok", "retcode": 0, "data": {"message_id": 8}}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        ok, detail = asyncio.run(
            self.card.send_json_card(
                "172.23.0.2:3000/",
                "",
                user_id="3482063535",
                payload=_cover_ready_payload(self.card),
                timeout=1.0,
            )
        )

        self.assertTrue(ok, detail)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][0].endswith("/send_private_msg"))
        self.assertTrue(calls[1][0].endswith("/send_msg"))
        self.assertEqual(calls[1][1]["message_type"], "private")
        self.assertEqual(calls[1][1]["user_id"], 3482063535)

    def test_real_rejection_does_not_retry(self):
        calls = []

        async def fake_post(url, body, token, timeout, *, use_header=True):
            calls.append(url)
            return {"status": "failed", "retcode": 1200, "message": "卡片被风控拦截"}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        ok, detail = asyncio.run(
            self.card.send_json_card(
                "http://127.0.0.1:3000",
                "",
                group_id="123",
                payload=_cover_ready_payload(self.card),
                timeout=1.0,
            )
        )

        self.assertFalse(ok)
        self.assertIn("风控", detail)
        self.assertEqual(len(calls), 1)

    def test_network_error_reports_after_retry(self):
        calls = []

        async def fake_post(url, body, token, timeout, *, use_header=True):
            calls.append(url)
            raise httpx.ConnectError("连接被拒绝")

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        ok, detail = asyncio.run(
            self.card.send_json_card(
                "http://127.0.0.1:3000",
                "",
                group_id="123",
                payload=_cover_ready_payload(self.card),
                timeout=1.0,
            )
        )

        self.assertFalse(ok)
        self.assertTrue(detail.startswith("请求失败："), detail)
        self.assertEqual(len(calls), 2)

    def test_fetch_mini_app_ark(self):
        calls = []
        card = {"ver": "1", "app": "com.tencent.miniapp_01", "view": "viewMultiMsg"}

        async def fake_post(url, body, token, timeout, *, use_header=True):
            calls.append((url, body, token))
            return {"status": "ok", "retcode": 0, "data": {"data": card}}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        got = asyncio.run(
            self.card.fetch_mini_app_ark(
                "http://172.23.0.2:3000",
                "tok",
                title="标题",
                jump_url="https://www.bilibili.com/video/BV1TEST",
                cover="//i0.hdslb.com/c.jpg",
                desc="测试UP",
                timeout=1.0,
            )
        )

        self.assertEqual(got, card)
        self.assertEqual(calls[0][0], "http://172.23.0.2:3000/get_mini_app_ark")
        self.assertEqual(calls[0][1]["type"], "bili")
        self.assertEqual(calls[0][2], "tok")
        self.assertEqual(calls[0][1]["picUrl"], "https://i0.hdslb.com/c.jpg")
        self.assertEqual(calls[0][1]["jumpUrl"], "https://www.bilibili.com/video/BV1TEST")

    def test_fetch_mini_app_ark_rejects_broken_reply(self):
        async def fake_post(url, body, token, timeout, *, use_header=True):
            return {"status": "ok", "retcode": 0, "data": {"data": {"ver": "1"}}}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        got = asyncio.run(
            self.card.fetch_mini_app_ark(
                "http://127.0.0.1:3000",
                "",
                title="标题",
                jump_url="https://www.bilibili.com/video/BV1TEST",
                timeout=1.0,
            )
        )
        self.assertEqual(got, {})

    def test_fetch_mini_app_ark_needs_url(self):
        got = asyncio.run(
            self.card.fetch_mini_app_ark("", "", title="标题", jump_url="u")
        )
        self.assertEqual(got, {})

    def test_looks_degraded(self):
        self.assertTrue(
            self.card.looks_degraded(
                {"message": [{"type": "text", "data": {"text": "发送者版本过低，无法展示内容"}}]}
            )
        )
        self.assertTrue(self.card.looks_degraded({"raw_message": "[unsupported:json]"}))
        self.assertFalse(
            self.card.looks_degraded(
                {"message": [{"type": "json", "data": {"data": "{}"}}]}
            )
        )
        self.assertFalse(self.card.looks_degraded({}))
        self.assertFalse(self.card.looks_degraded("plain"))

    def test_check_card_message(self):
        replies = {
            "1": {"status": "ok", "retcode": 0, "data": {"message_id": 1, "raw_message": "[CQ:json]"}},
            "2": {
                "status": "ok",
                "retcode": 0,
                "data": {"message_id": 2, "raw_message": "发送者版本过低，无法展示内容"},
            },
        }

        async def fake_post(url, body, token, timeout, *, use_header=True):
            self.assertTrue(url.endswith("/get_msg"))
            return replies.get(str(body.get("message_id")), {"status": "failed", "retcode": 100})

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        self.assertIs(
            asyncio.run(self.card.check_card_message("http://127.0.0.1:3000", "", "1")), True
        )
        self.assertIs(
            asyncio.run(self.card.check_card_message("http://127.0.0.1:3000", "", "2")), False
        )
        self.assertIsNone(asyncio.run(self.card.check_card_message("http://127.0.0.1:3000", "", "")))

    def test_recall_message(self):
        calls = []

        async def fake_post(url, body, token, timeout, *, use_header=True):
            calls.append((url, body))
            return {"status": "ok", "retcode": 0, "data": None}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        self.assertTrue(
            asyncio.run(self.card.recall_message("http://127.0.0.1:3000", "", "259010366"))
        )
        self.assertTrue(calls[0][0].endswith("/delete_msg"))
        self.assertEqual(calls[0][1]["message_id"], 259010366)
        self.assertFalse(asyncio.run(self.card.recall_message("", "", "1")))

    def test_send_tuwen_ark_group(self):
        calls = []

        async def fake_post(url, body, token, timeout, *, use_header=True):
            calls.append((url, body, token))
            return {"status": "ok", "retcode": 0, "data": {"message_id": 987}}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        ok, detail, message_id = asyncio.run(
            self.card.send_tuwen_ark(
                "http://172.23.0.2:3000",
                "tok",
                group_id="1083138200",
                title="标题",
                desc="测试UP",
                jump_url="https://www.bilibili.com/video/BV1TEST",
                preview_url="//i0.hdslb.com/c.jpg",
                timeout=1.0,
            )
        )

        self.assertTrue(ok, detail)
        self.assertEqual(message_id, "987")
        self.assertTrue(calls[0][0].endswith("/send_tuwen_ark"))
        self.assertEqual(calls[0][2], "tok")
        body = calls[0][1]
        self.assertEqual(body["group_id"], 1083138200)
        self.assertNotIn("user_id", body)
        self.assertEqual(body["title"], "标题")
        self.assertEqual(body["desc"], "测试UP")
        self.assertEqual(body["jump_url"], "https://www.bilibili.com/video/BV1TEST")
        self.assertEqual(body["preview_url"], "https://i0.hdslb.com/c.jpg")
        self.assertEqual(body["summary"], "[分享]")

    def test_send_tuwen_ark_private(self):
        calls = []

        async def fake_post(url, body, token, timeout, *, use_header=True):
            calls.append(body)
            return {"status": "ok", "retcode": 0, "data": {}}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        ok, _, message_id = asyncio.run(
            self.card.send_tuwen_ark(
                "http://172.23.0.2:3000",
                "",
                user_id="3482063535",
                title="标题",
                jump_url="https://www.bilibili.com/video/BV1TEST",
                timeout=1.0,
            )
        )
        self.assertTrue(ok)
        self.assertEqual(message_id, "")
        self.assertEqual(calls[0]["user_id"], 3482063535)
        self.assertNotIn("group_id", calls[0])
        # 没有封面时不传 preview_url，让服务端用默认图
        self.assertNotIn("preview_url", calls[0])

    def test_send_tuwen_ark_validates_input(self):
        self.assertFalse(asyncio.run(self.card.send_tuwen_ark("", "", group_id="1", title="t", jump_url="u"))[0])
        self.assertFalse(
            asyncio.run(self.card.send_tuwen_ark("http://x", "", group_id="1", title="", jump_url="u"))[0]
        )
        self.assertFalse(
            asyncio.run(self.card.send_tuwen_ark("http://x", "", group_id="", user_id="", title="t", jump_url="u"))[0]
        )

    def test_send_tuwen_ark_reports_rejection(self):
        async def fake_post(url, body, token, timeout, *, use_header=True):
            return {"status": "failed", "retcode": 1404, "message": "unknown action"}

        original = self.card._post
        self.card._post = fake_post
        self.addCleanup(lambda: setattr(self.card, "_post", original))

        ok, detail, message_id = asyncio.run(
            self.card.send_tuwen_ark(
                "http://x", "", group_id="1", title="t", jump_url="https://u", timeout=1.0
            )
        )
        self.assertFalse(ok)
        self.assertIn("unknown action", detail)
        self.assertEqual(message_id, "")

    def test_message_id(self):
        self.assertEqual(self.card._message_id({"data": {"message_id": 12}}), "12")
        self.assertEqual(self.card._message_id({"data": {}}), "")
        self.assertEqual(self.card._message_id("nope"), "")


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

    async def test_image_style_sends_cover_then_text(self):
        plugin = self._plugin(share={"style": "image"})
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        ok = await plugin._send_parts("stream-1", video, "看看这个")
        self.assertTrue(ok)
        self.assertEqual(len(self.ctx.send.images), 1)
        self.assertEqual(
            base64.b64decode(self.ctx.send.images[0]["data"]), b"cover-bytes"
        )
        self.assertIn("【原神新版本测评】", self.ctx.send.messages[0]["text"])

    async def test_image_style_falls_back_without_cover(self):
        plugin = self._plugin(share={"style": "image"})
        video = make_video(self.video_module)  # 没有封面
        self._cover(plugin, None)
        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(self.ctx.send.images, [])
        self.assertIn("【原神新版本测评】", self.ctx.send.messages[0]["text"])

    def _stub_card(self, *, ok=True, detail="ok", message_id="", ark=None, verdict=None):
        """把卡片相关的网络调用全换成桩，别让测试真去连 QQ 客户端。"""

        module = self.module
        calls = {"send": [], "ark": [], "check": [], "recall": [], "tuwen": []}

        async def fake_tuwen(api_base, token="", **kwargs):
            calls["tuwen"].append({"api_base": api_base, "token": token, **kwargs})
            return ok, detail, message_id

        async def fake_ark(api_base, token="", **kwargs):
            calls["ark"].append({"api_base": api_base, "token": token, **kwargs})
            return dict(ark or {})

        async def fake_send(api_base, token="", **kwargs):
            calls["send"].append({"api_base": api_base, "token": token, **kwargs})
            return ok, detail, message_id

        async def fake_check(api_base, token="", mid="", timeout=5.0):
            calls["check"].append(mid)
            return verdict

        async def fake_recall(api_base, token="", mid="", timeout=5.0):
            calls["recall"].append(mid)
            return True

        for name, value in (
            ("fetch_mini_app_ark", fake_ark),
            ("send_json_card_ex", fake_send),
            ("send_tuwen_ark", fake_tuwen),
            ("check_card_message", fake_check),
            ("recall_message", fake_recall),
        ):
            original = getattr(module, name)
            setattr(module, name, value)
            self.addCleanup(lambda n=name, o=original: setattr(module, n, o))
        return calls

    async def test_card_style_uses_onebot_route(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000", "access_token": "tok"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        calls = self._stub_card()

        ok = await plugin._send_parts("stream-1", video, "快看这个")
        self.assertTrue(ok)
        self.assertEqual(len(calls["send"]), 1)
        sent = calls["send"][0]
        self.assertEqual(sent["api_base"], "http://172.23.0.2:3000")
        self.assertEqual(sent["token"], "tok")
        self.assertEqual(sent["group_id"], "123456")
        self.assertEqual(sent["payload"]["app"], "com.tencent.miniapp_01")
        # 卡片前面那句随口话照发
        self.assertEqual(len(self.ctx.send.messages), 1)
        self.assertIn("快看这个", self.ctx.send.messages[0]["text"])

    async def test_card_style_prefers_official_ark(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000", "access_token": "tok"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        ark = {"app": "com.tencent.miniapp_01", "view": "viewMultiMsg", "meta": {"detail_1": {}}}
        calls = self._stub_card(ark=ark)

        ok = await plugin._send_parts("stream-1", video, "快看这个")
        self.assertTrue(ok)
        self.assertEqual(len(calls["ark"]), 1)
        self.assertEqual(calls["ark"][0]["title"], video.title)
        self.assertEqual(calls["ark"][0]["jump_url"], video.url)
        self.assertEqual(calls["ark"][0]["cover"], video.cover)
        # 发出去的是官方通道生成的那张卡，不是本地拼的
        self.assertEqual(calls["send"][0]["payload"], ark)

    async def test_card_style_falls_back_to_local_payload(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        calls = self._stub_card(ark=None)

        ok = await plugin._send_parts("stream-1", video, "快看这个")
        self.assertTrue(ok)
        self.assertEqual(len(calls["ark"]), 1)
        payload = calls["send"][0]["payload"]
        self.assertEqual(payload["app"], "com.tencent.miniapp_01")
        self.assertEqual(payload["view"], "viewMultiMsg")
        self.assertEqual(
            payload["meta"]["detail_1"]["preview"], "https://i0.hdslb.com/c.jpg"
        )

    async def test_card_style_can_send_ark_without_cover(self):
        # 本地拼卡片必须有封面，但官方通道只要标题 + 链接就能出卡
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module)  # 没有封面
        ark = {"app": "com.tencent.miniapp_01", "view": "viewMultiMsg"}
        calls = self._stub_card(ark=ark)

        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(len(calls["send"]), 1)
        self.assertEqual(calls["send"][0]["payload"], ark)

    async def test_news_kind_ignores_official_ark(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000", "card_kind": "news"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        calls = self._stub_card()

        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(calls["ark"], [])
        self.assertEqual(calls["send"][0]["payload"]["view"], "news")

    async def test_degraded_card_is_recalled_and_falls_back(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        calls = self._stub_card(message_id="259010366", verdict=False)

        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(calls["check"], ["259010366"])
        self.assertEqual(calls["recall"], ["259010366"])
        # 撤回后自动改发封面图 + 文字
        self.assertEqual(len(self.ctx.send.images), 1)
        self.assertIn("【原神新版本测评】", self.ctx.send.messages[-1]["text"])

    async def test_degraded_card_check_can_be_disabled(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000", "verify_send": False},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        calls = self._stub_card(message_id="259010366", verdict=False)

        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(calls["check"], [])
        self.assertEqual(self.ctx.send.images, [])

    async def test_card_style_without_api_falls_back_to_image(self):
        plugin = self._plugin(share={"style": "card"})  # 没配卡片直发地址
        plugin._register_stream_route("stream-1", user_id="3482063535")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        self._stub_card()
        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(len(self.ctx.send.images), 1)
        self.assertIn("看看", self.ctx.send.messages[0]["text"])

    async def test_card_style_without_route_falls_back_to_text(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000", "fallback_to_image": False},
        )
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin, None)
        self._stub_card()
        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(self.ctx.send.images, [])
        self.assertIn("【原神新版本测评】", self.ctx.send.messages[0]["text"])

    async def test_card_style_without_caption_sends_no_text(self):
        plugin = self._plugin(
            share={"style": "card", "card_caption": False},
            card={"api_base": "http://172.23.0.2:3000"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._stub_card()

        ok = await plugin._send_parts("stream-1", video, "快看这个")
        self.assertTrue(ok)
        self.assertEqual(self.ctx.send.messages, [])

    async def test_image_style_respects_shiwei_no_narrative(self):
        plugin = self._plugin(share={"style": "image"})
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        await plugin._send_parts("stream-1", video, "这段文案不该出现", kind="shiwei")
        self.assertNotIn("这段文案不该出现", self.ctx.send.messages[0]["text"])

    async def test_tuwen_kind_sends_native_ark(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={
                "api_base": "http://172.23.0.2:3000",
                "access_token": "tok",
                "card_kind": "tuwen",
            },
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        calls = self._stub_card(message_id="999", verdict=True)

        ok = await plugin._send_parts("stream-1", video, "快看这个")
        self.assertTrue(ok)
        # 图文 Ark 不走 json 段、也不调小程序通道
        self.assertEqual(calls["send"], [])
        self.assertEqual(calls["ark"], [])
        self.assertEqual(len(calls["tuwen"]), 1)
        sent = calls["tuwen"][0]
        self.assertEqual(sent["api_base"], "http://172.23.0.2:3000")
        self.assertEqual(sent["token"], "tok")
        self.assertEqual(sent["group_id"], "123456")
        self.assertEqual(sent["user_id"], "")
        self.assertEqual(sent["title"], video.title)
        self.assertEqual(sent["jump_url"], video.url)
        self.assertEqual(sent["preview_url"], video.cover)
        self.assertEqual(calls["check"], ["999"])
        self.assertEqual(len(self.ctx.send.messages), 1)
        self.assertIn("快看这个", self.ctx.send.messages[0]["text"])

    async def test_tuwen_degraded_falls_back_to_image(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000", "card_kind": "tuwen"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        calls = self._stub_card(message_id="259010366", verdict=False)

        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(calls["recall"], ["259010366"])
        self.assertEqual(len(self.ctx.send.images), 1)

    async def test_tuwen_failure_falls_back_to_image(self):
        plugin = self._plugin(
            share={"style": "card"},
            card={"api_base": "http://172.23.0.2:3000", "card_kind": "tuwen"},
        )
        plugin._register_stream_route("stream-1", group_id="123456")
        video = make_video(self.video_module, cover="//i0.hdslb.com/c.jpg")
        self._cover(plugin)
        calls = self._stub_card(ok=False, detail="不支持")

        ok = await plugin._send_parts("stream-1", video, "看看")
        self.assertTrue(ok)
        self.assertEqual(len(calls["tuwen"]), 1)
        self.assertEqual(len(self.ctx.send.images), 1)

