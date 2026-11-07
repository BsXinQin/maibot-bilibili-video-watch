"""bili_read（读视频）模块与插件入口的单元测试。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import FakeCtx, load_plugin_module, load_submodule  # noqa: E402

READ = load_submodule("bili_read")


async def _no_vision(brief):
    """测试桩：不做抽帧描述。"""
    return ""


class _Comment:
    def __init__(self, message: str, user: str = "", like: int = 0) -> None:
        self.message = message
        self.user = user
        self.like = like


class _Danmaku:
    def __init__(self, text: str, progress_ms: int = 0) -> None:
        self.text = text
        self.progress_ms = progress_ms


def sample_view(**overrides):
    data = {
        "bvid": "BV1TEST00001",
        "aid": 123456,
        "title": "原神新版本测评",
        "desc": "本期聊聊原神新版本",
        "owner": {"name": "测试UP", "mid": 42},
        "tname": "游戏",
        "duration": 3725,
        "pubdate": 1730000000,
        "cid": 111,
        "tag": ["原神", "游戏", "测评"],
        "stat": {
            "view": 120000,
            "danmaku": 500,
            "reply": 800,
            "like": 8000,
            "coin": 2000,
            "favorite": 1500,
        },
        "pages": [
            {"part": "P1 开场", "duration": 300, "cid": 111},
            {"part": "P2 实战", "duration": 400, "cid": 222},
        ],
    }
    data.update(overrides)
    return data


def sample_bundle(**overrides):
    view = sample_view()
    bundle = {
        "view": view,
        "pages": view["pages"],
        "page": 1,
        "comments": [_Comment("这个改动好大", "路人甲", 1200), _Comment("", "空评论", 0)],
        "danmaku": [_Danmaku("前方高能")],
        "subtitle": "欢迎大家收看本期节目",
    }
    bundle.update(overrides)
    return bundle


class _FakeReadClient:
    """只实现读视频需要的两个方法的假客户端。"""

    def __init__(self, bundle, *, final_url="", fetch_error=None, redirect_error=None) -> None:
        self.bundle = bundle
        self.final_url = final_url
        self.fetch_error = fetch_error
        self.redirect_error = redirect_error
        self.fetch_calls = []
        self.redirects = []

    async def fetch_video_bundle(self, **kwargs):
        self.fetch_calls.append(kwargs)
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.bundle

    async def resolve_redirect(self, url):
        self.redirects.append(url)
        if self.redirect_error is not None:
            raise self.redirect_error
        return self.final_url


class ParseTargetTests(unittest.TestCase):
    def test_full_url_with_page(self) -> None:
        target = READ.parse_target("看看这个 https://www.bilibili.com/video/BV1nr476pEbE?p=3 挺好")
        self.assertIsNotNone(target)
        self.assertEqual(target.bvid, "BV1nr476pEbE")
        self.assertEqual(target.page, 3)
        self.assertEqual(target.key, "BV1nr476pEbE:p3")

    def test_video_url_without_page(self) -> None:
        target = READ.parse_target("https://www.bilibili.com/video/BV1nr476pEbE/")
        self.assertEqual(target.bvid, "BV1nr476pEbE")
        self.assertEqual(target.page, 1)

    def test_av_url(self) -> None:
        target = READ.parse_target("https://www.bilibili.com/video/av170001")
        self.assertEqual(target.aid, 170001)
        self.assertEqual(target.key, "av170001:p1")

    def test_bare_bvid(self) -> None:
        target = READ.parse_target("这个 BV1nr476pEbE 值得一看")
        self.assertEqual(target.bvid, "BV1nr476pEbE")

    def test_short_link(self) -> None:
        target = READ.parse_target("https://b23.tv/abcd123")
        self.assertEqual(target.short_url, "https://b23.tv/abcd123")
        self.assertEqual(target.bvid, "")
        self.assertFalse(target.empty)

    def test_ignores_other_sites(self) -> None:
        self.assertIsNone(READ.parse_target("https://example.com/watch?v=1"))

    def test_empty_text(self) -> None:
        self.assertIsNone(READ.parse_target("   "))
        self.assertIsNone(READ.parse_target(None))

    def test_empty_target_property(self) -> None:
        self.assertTrue(READ.VideoTarget().empty)


class ExtractTargetsTests(unittest.TestCase):
    def test_orders_by_position(self) -> None:
        text = "先看 BV1nr476pEbE 再看 https://www.bilibili.com/video/BV1aaaaaaaaa"
        targets = READ.extract_targets(text)
        self.assertEqual(targets, ["BV1nr476pEbE", "https://www.bilibili.com/video/BV1aaaaaaaaa"])

    def test_dedupes_same_video(self) -> None:
        text = "https://www.bilibili.com/video/BV1nr476pEbE 和 BV1nr476pEbE 是同一个"
        self.assertEqual(len(READ.extract_targets(text)), 1)

    def test_respects_limit(self) -> None:
        text = "BV1nr476pEbE BV1aaaaaaaaa BV1bbbbbbbbb"
        self.assertEqual(len(READ.extract_targets(text, limit=2)), 2)

    def test_ignores_non_bilibili_links(self) -> None:
        self.assertEqual(READ.extract_targets("https://example.com/a 和 https://example.com/b"), [])


class BuildBriefTests(unittest.TestCase):
    def test_build_from_bundle(self) -> None:
        brief = READ.build_brief(sample_bundle(), target=READ.VideoTarget(bvid="BV1TEST00001"))
        self.assertEqual(brief.bvid, "BV1TEST00001")
        self.assertEqual(brief.aid, 123456)
        self.assertEqual(brief.title, "原神新版本测评")
        self.assertEqual(brief.owner, "测试UP")
        self.assertEqual(brief.partition, "游戏")
        self.assertEqual(brief.duration, 3725)
        self.assertEqual(brief.tags, ["原神", "游戏", "测评"])
        self.assertEqual(len(brief.pages), 2)
        self.assertEqual(brief.page, 1)
        self.assertEqual(brief.stats["play"], 120000)
        self.assertEqual(len(brief.comments), 1)
        self.assertEqual(brief.comments[0][1], "路人甲")
        self.assertEqual(brief.danmaku, ["前方高能"])
        self.assertEqual(brief.subtitle, "欢迎大家收看本期节目")
        self.assertEqual(brief.part_title, "P1 开场")

    def test_missing_view_does_not_crash(self) -> None:
        brief = READ.build_brief({"comments": [_Comment("只有评论")]})
        self.assertEqual(brief.bvid, "")
        self.assertEqual(len(brief.comments), 1)
        self.assertEqual(brief.stats["play"], 0)

    def test_tag_string_is_split(self) -> None:
        bundle = sample_bundle(view=sample_view(tag="原神, 游戏"))
        brief = READ.build_brief(bundle)
        self.assertEqual(brief.tags, ["原神", "游戏"])

    def test_to_dict(self) -> None:
        brief = READ.build_brief(sample_bundle())
        data = brief.to_dict()
        self.assertEqual(data["bvid"], "BV1TEST00001")
        self.assertEqual(data["pages"], 2)
        self.assertEqual(data["danmaku"], 1)
        self.assertTrue(data["has_subtitle"])

    def test_builds_danmaku_and_subtitle_timelines(self) -> None:
        bundle = sample_bundle(
            danmaku=[_Danmaku("前方高能", 61_500), _Danmaku("这里绝了", 65_000)],
            subtitle_segments=[(0, "第一段"), (95, "第二段")],
        )
        brief = READ.build_brief(bundle)
        self.assertEqual(brief.danmaku, ["前方高能", "这里绝了"])
        self.assertEqual(brief.danmaku_timeline, [(61, "前方高能"), (65, "这里绝了")])
        self.assertEqual(brief.subtitle_segments, [(0, "第一段"), (95, "第二段")])
        self.assertTrue(brief.to_dict()["has_danmaku_timeline"])
        self.assertTrue(brief.to_dict()["has_subtitle_timeline"])


class RenderBriefTests(unittest.TestCase):
    def test_key_lines(self) -> None:
        brief = READ.build_brief(sample_bundle())
        text = READ.render_brief(brief)
        self.assertTrue(text.startswith("【B站视频】原神新版本测评"))
        self.assertIn("UP主 测试UP · 分区 游戏 · 时长 1:02:05", text)
        self.assertIn("播放 12.0万", text)
        self.assertIn("发布：", text)
        self.assertIn("分P：共 2 P（当前 P1：P1 开场）", text)
        self.assertIn("分P列表：P1 P1 开场、P2 P2 实战", text)
        self.assertIn("标签：原神 / 游戏 / 测评", text)
        self.assertIn("简介：本期聊聊原神新版本", text)
        self.assertIn("字幕（节选）：欢迎大家收看本期节目", text)
        self.assertIn("1. （1200 赞）路人甲：这个改动好大", text)
        self.assertIn("弹幕采样：前方高能", text)
        self.assertTrue(text.endswith("链接：https://www.bilibili.com/video/BV1TEST00001"))

    def test_page_two_url_and_part(self) -> None:
        brief = READ.build_brief(sample_bundle(), target=READ.VideoTarget(bvid="BV1TEST00001", page=2))
        brief.page = 2
        self.assertEqual(brief.part_title, "P2 实战")
        text = READ.render_brief(brief)
        self.assertIn("当前 P2：P2 实战", text)
        self.assertTrue(text.rstrip().endswith("?p=2"))

    def test_description_truncated(self) -> None:
        bundle = sample_bundle(view=sample_view(desc="长" * 400))
        text = READ.render_brief(READ.build_brief(bundle))
        line = [item for item in text.splitlines() if item.startswith("简介：")][0]
        self.assertEqual(line, "简介：" + "长" * 300 + "……")

    def test_max_chars_truncates(self) -> None:
        bundle = sample_bundle(
            view=sample_view(desc="长" * 400),
            danmaku=[_Danmaku("弹" * 50) for _ in range(10)],
        )
        text = READ.render_brief(READ.build_brief(bundle), max_chars=500)
        self.assertLessEqual(len(text), 500)
        self.assertTrue(text.endswith("……（已截断）"))

    def test_danmaku_sampled_and_shortened(self) -> None:
        long_line = "弹" * 60
        bundle = sample_bundle(danmaku=[_Danmaku(long_line) for _ in range(40)])
        text = READ.render_brief(READ.build_brief(bundle))
        line = [item for item in text.splitlines() if item.startswith("弹幕采样：")][0]
        items = line[len("弹幕采样："):].split(" / ")
        self.assertEqual(len(items), 24)
        for item in items:
            self.assertLessEqual(len(item), 24)

    def test_full_detail_renders_subtitle_timeline(self) -> None:
        bundle = sample_bundle(
            subtitle_segments=[(0, "第一句"), (30, "第二句"), (95, "第三句")],
        )
        text = READ.render_brief(READ.build_brief(bundle), detail="full", max_chars=9000)
        self.assertIn("深度读：完整字幕时间轴", text)
        self.assertIn("字幕时间轴（", text)
        self.assertIn("[00:00] 第一句 第二句", text)
        self.assertIn("[01:30] 第三句", text)
        self.assertNotIn("字幕（节选）：", text)

    def test_full_detail_aggregates_danmaku_by_minute(self) -> None:
        bundle = sample_bundle(
            danmaku=[
                _Danmaku("哈哈哈", 5_000),
                _Danmaku("前方高能", 61_000),
                _Danmaku("这里绝了", 65_000),
            ],
        )
        text = READ.render_brief(READ.build_brief(bundle), detail="full", max_chars=9000)
        self.assertIn("弹幕时间线（按分钟", text)
        self.assertIn("[00:00] 1 条：哈哈哈", text)
        self.assertIn("[01:00] 2 条：前方高能 / 这里绝了", text)
        self.assertNotIn("弹幕采样：", text)

    def test_full_detail_lists_all_pages_and_more_comments(self) -> None:
        pages = [{"part": f"第{i}话", "duration": 60, "cid": i} for i in range(1, 13)]
        comments = [_Comment(f"评论{i}", f"用户{i}", 100 - i) for i in range(1, 15)]
        bundle = sample_bundle(view=sample_view(pages=pages), pages=pages, comments=comments)
        text = READ.render_brief(READ.build_brief(bundle), detail="full", max_chars=9000)
        self.assertIn("P12 第12话", text)
        self.assertIn("14. （86 赞）用户14：评论14", text)

    def test_full_detail_falls_back_to_plain_subtitle_and_sampled_danmaku(self) -> None:
        brief = READ.VideoBrief(
            title="只有纯文本", bvid="BV1TEST00001", subtitle="纯字幕", danmaku=["弹幕一"]
        )
        text = READ.render_brief(brief, detail="full")
        self.assertIn("字幕（全文）：纯字幕", text)
        self.assertIn("弹幕采样：弹幕一", text)

    def test_without_title_falls_back_to_bvid(self) -> None:
        bundle = sample_bundle(view=sample_view(title="", desc=""))
        text = READ.render_brief(READ.build_brief(bundle))
        self.assertTrue(text.startswith("【B站视频】BV1TEST00001"))


class VideoCacheTests(unittest.TestCase):
    def test_put_and_get(self) -> None:
        cache = READ.VideoCache(ttl_seconds=10)
        brief = READ.build_brief(sample_bundle())
        cache.put("BV1:p1", brief, now=100.0)
        self.assertIs(cache.get("BV1:p1", now=105.0), brief)
        self.assertEqual(len(cache), 1)

    def test_expires(self) -> None:
        cache = READ.VideoCache(ttl_seconds=10)
        cache.put("BV1:p1", READ.build_brief(sample_bundle()), now=100.0)
        self.assertIsNone(cache.get("BV1:p1", now=111.0))
        self.assertEqual(len(cache), 0)

    def test_evicts_oldest_when_full(self) -> None:
        cache = READ.VideoCache(ttl_seconds=600, max_items=2)
        brief = READ.build_brief(sample_bundle())
        cache.put("first", brief, now=1.0)
        cache.put("second", brief, now=2.0)
        cache.put("third", brief, now=3.0)
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get("first", now=4.0))
        self.assertIsNotNone(cache.get("third", now=4.0))

    def test_clear(self) -> None:
        cache = READ.VideoCache()
        cache.put("k", READ.build_brief(sample_bundle()))
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_cache_key_depends_on_params(self) -> None:
        target = READ.VideoTarget(bvid="BV1TEST00001")
        base_key = READ.cache_key(target, comment_limit=3, danmaku_limit=5, use_subtitle=True)
        self.assertEqual(base_key, "BV1TEST00001:p1|c3|d5|s1")
        self.assertNotEqual(
            base_key,
            READ.cache_key(target, comment_limit=4, danmaku_limit=5, use_subtitle=True),
        )
        self.assertNotEqual(
            base_key,
            READ.cache_key(target, comment_limit=3, danmaku_limit=5, use_subtitle=False),
        )
        deep_key = READ.cache_key(
            target, comment_limit=3, danmaku_limit=5, use_subtitle=True, subtitle_chars=12000
        )
        self.assertNotEqual(base_key, deep_key)
        self.assertTrue(deep_key.endswith(".12000"))


class ReadBriefTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_and_passes_limits(self) -> None:
        client = _FakeReadClient(sample_bundle())
        target = READ.parse_target("BV1TEST00001")
        brief = await READ.read_brief(client, target, comment_limit=3, danmaku_limit=5)
        self.assertEqual(brief.title, "原神新版本测评")
        self.assertEqual(
            client.fetch_calls,
            [
                {
                    "bvid": "BV1TEST00001",
                    "aid": 0,
                    "page": 1,
                    "comment_limit": 3,
                    "danmaku_limit": 5,
                    "subtitle_chars": 1200,
                }
            ],
        )

    async def test_cache_hit_skips_second_fetch(self) -> None:
        client = _FakeReadClient(sample_bundle())
        cache = READ.VideoCache(ttl_seconds=600)
        target = READ.parse_target("BV1TEST00001")
        first = await READ.read_brief(client, target, cache=cache)
        second = await READ.read_brief(client, target, cache=cache)
        self.assertIs(first, second)
        self.assertEqual(len(client.fetch_calls), 1)

    async def test_short_link_is_resolved(self) -> None:
        client = _FakeReadClient(
            sample_bundle(), final_url="https://www.bilibili.com/video/BV1TEST00001?p=2"
        )
        target = READ.parse_target("https://b23.tv/abcd123")
        brief = await READ.read_brief(client, target)
        self.assertEqual(client.redirects, ["https://b23.tv/abcd123"])
        self.assertEqual(brief.bvid, "BV1TEST00001")
        self.assertEqual(client.fetch_calls[0]["page"], 2)

    async def test_short_link_failure(self) -> None:
        client = _FakeReadClient(
            sample_bundle(), redirect_error=READ.BilibiliError("HTTP 404")
        )
        with self.assertRaises(READ.BiliReadError) as caught:
            await READ.read_brief(client, READ.parse_target("https://b23.tv/abcd123"))
        self.assertIn("短链解析失败", str(caught.exception))

    async def test_short_link_without_video(self) -> None:
        client = _FakeReadClient(sample_bundle(), final_url="https://www.bilibili.com/")
        with self.assertRaises(READ.BiliReadError) as caught:
            await READ.read_brief(client, READ.parse_target("https://b23.tv/abcd123"))
        self.assertIn("没有指向 B 站视频", str(caught.exception))

    async def test_fetch_error_wrapped(self) -> None:
        client = _FakeReadClient(sample_bundle(), fetch_error=READ.BilibiliError("code=-404"))
        with self.assertRaises(READ.BiliReadError) as caught:
            await READ.read_brief(client, READ.parse_target("BV1TEST00001"))
        self.assertIn("读取视频失败", str(caught.exception))

    async def test_empty_target(self) -> None:
        with self.assertRaises(READ.BiliReadError):
            await READ.read_brief(_FakeReadClient(sample_bundle()), READ.VideoTarget())


class _FakeAppender:
    def __init__(self) -> None:
        self.calls = []

    async def append(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True}


class PluginReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.module = load_plugin_module()
        self.appender = _FakeAppender()
        self.ctx = FakeCtx(self.tmp_dir)
        self.ctx.maisaka = SimpleNamespace(context=self.appender)
        self.client = _FakeReadClient(sample_bundle())
        self.plugin = self._make_plugin()

    def _make_plugin(self, **overrides):
        config = {
            "plugin": {"enabled": True, "config_version": "1.2.0"},
            "read": {
                "enabled": True,
                "auto_read_enabled": True,
                "auto_read_max_links": 1,
                "comment_limit": 2,
                "danmaku_limit": 3,
                "cache_seconds": 900,
            },
            "streams": [{"name": "测试群", "enabled": True, "stream_id": "stream-1"}],
        }
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value
        plugin = self.module.BilibiliInterestSharePlugin()
        plugin._set_context(self.ctx)
        plugin.set_plugin_config(config)
        plugin._client = self.client
        plugin._read_cache = None
        plugin._auto_read_seen.clear()
        return plugin

    def test_get_components_exposes_tool_with_chat_scope(self) -> None:
        components = self.plugin.get_components()
        tool = [item for item in components if item.get("name") == "bili_read"]
        self.assertEqual(len(tool), 1)
        self.assertEqual(tool[0].get("chat_scope"), "all")
        metadata = tool[0].get("metadata") or {}
        self.assertEqual(metadata.get("visibility"), "visible")
        self.assertIs(metadata.get("core_tool"), True)
        self.assertIn("子代理", metadata.get("description", ""))
        names = [item.get("name") for item in components]
        self.assertNotIn("bili_read_cmd", names, "聊天指令已移除")
        for name in ("bili_search", "bili_shiwei"):
            tool = [item for item in components if item.get("name") == name]
            self.assertEqual(len(tool), 1, name)
            tool_meta = tool[0].get("metadata") or {}
            self.assertEqual(tool[0].get("chat_scope"), "all", name)
            self.assertIs(tool_meta.get("core_tool"), True, name)

    async def test_tool_reads_video(self) -> None:
        result = await self.plugin.tool_bili_read(url="BV1TEST00001")
        self.assertTrue(result["success"], result)
        self.assertIn("【B站视频】原神新版本测评", result["content"])
        self.assertEqual(result["bvid"], "BV1TEST00001")
        self.assertEqual(result["url"], "https://www.bilibili.com/video/BV1TEST00001")

    async def test_tool_falls_back_to_kwargs(self) -> None:
        result = await self.plugin.tool_bili_read(url="", text="帮我看看 BV1TEST00001")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["bvid"], "BV1TEST00001")

    async def test_tool_detail_full_uses_deep_limits(self) -> None:
        result = await self.plugin.tool_bili_read(url="BV1TEST00001", detail="full")
        self.assertTrue(result["success"], result)
        call = self.client.fetch_calls[-1]
        self.assertEqual(call["comment_limit"], 15)
        self.assertEqual(call["danmaku_limit"], 120)
        self.assertEqual(call["subtitle_chars"], 12000)
        self.assertIn("深度读：完整字幕时间轴", result["content"])

    async def test_tool_without_target(self) -> None:
        result = await self.plugin.tool_bili_read(url="")
        self.assertFalse(result["success"])
        self.assertIn("缺少视频链接", result["error"])

    async def test_tool_with_unknown_text(self) -> None:
        result = await self.plugin.tool_bili_read(url="随便说点啥")
        self.assertFalse(result["success"])
        self.assertIn("没认出", result["error"])

    async def test_tool_disabled(self) -> None:
        plugin = self._make_plugin(read={"enabled": False})
        result = await plugin.tool_bili_read(url="BV1TEST00001")
        self.assertFalse(result["success"])
        self.assertIn("关闭", result["error"])

    async def test_tool_reports_api_error(self) -> None:
        self.client.fetch_error = READ.BilibiliError("code=-404：啥都木有")
        result = await self.plugin.tool_bili_read(url="BV1TEST00001")
        self.assertFalse(result["success"])
        self.assertIn("读取视频失败", result["error"])

    async def test_auto_read_appends_context_once(self) -> None:
        message = {
            "stream_id": "stream-1",
            "processed_plain_text": "看看这个 https://www.bilibili.com/video/BV1TEST00001",
        }
        await self.plugin._maybe_auto_read(message)
        self.assertEqual(len(self.appender.calls), 1)
        call = self.appender.calls[0]
        self.assertEqual(call["stream_id"], "stream-1")
        self.assertEqual(call["source_kind"], "plugin:bilibili_interest_share")
        self.assertIn("原神新版本测评", call["visible_text"])
        self.assertIn("【B站视频】", call["segments"][0]["content"])
        self.assertIn("不用再调用 bili_read", call["segments"][0]["content"])

        await self.plugin._maybe_auto_read(message)
        self.assertEqual(len(self.appender.calls), 1)

    async def test_auto_read_skips_other_streams(self) -> None:
        message = {
            "stream_id": "stream-9",
            "processed_plain_text": "https://www.bilibili.com/video/BV1TEST00001",
        }
        await self.plugin._maybe_auto_read(message)
        self.assertEqual(self.appender.calls, [])

    async def test_auto_read_skips_share_messages(self) -> None:
        message = {
            "stream_id": "stream-1",
            "processed_plain_text": "【原神新版本测评】https://www.bilibili.com/video/BV1TEST00001",
        }
        await self.plugin._maybe_auto_read(message)
        self.assertEqual(self.appender.calls, [])

    async def test_auto_read_skips_plain_messages(self) -> None:
        await self.plugin._maybe_auto_read({"stream_id": "stream-1", "processed_plain_text": "在吗"})
        self.assertEqual(self.appender.calls, [])

    async def test_auto_read_disabled(self) -> None:
        plugin = self._make_plugin(read={"auto_read_enabled": False})
        plugin._client = self.client
        await plugin._maybe_auto_read(
            {
                "stream_id": "stream-1",
                "processed_plain_text": "https://www.bilibili.com/video/BV1TEST00001",
            }
        )
        self.assertEqual(self.appender.calls, [])

    async def test_auto_read_survives_read_failure(self) -> None:
        self.client.fetch_error = READ.BilibiliError("code=-404")
        await self.plugin._maybe_auto_read(
            {
                "stream_id": "stream-1",
                "processed_plain_text": "https://www.bilibili.com/video/BV1TEST00001",
            }
        )
        self.assertEqual(self.appender.calls, [])

    async def test_auto_read_full_when_keyword(self) -> None:
        called = {}

        async def fake_describe(brief):
            called["bvid"] = brief.bvid
            return "画面上有人在打怪"

        self.plugin._describe_video_frames = fake_describe
        await self.plugin._maybe_auto_read(
            {
                "stream_id": "stream-1",
                "processed_plain_text": "详细看看 https://www.bilibili.com/video/BV1TEST00001",
            }
        )
        self.assertEqual(called.get("bvid"), "BV1TEST00001")
        self.assertEqual(len(self.appender.calls), 1)
        content = self.appender.calls[0]["segments"][0]["content"]
        self.assertIn("深度读", content)
        self.assertIn("【画面描述（抽帧）】", content)
        self.assertIn("画面上有人在打怪", content)

    async def test_auto_read_full_when_mentioned(self) -> None:
        self.plugin._describe_video_frames = _no_vision
        await self.plugin._maybe_auto_read(
            {
                "stream_id": "stream-1",
                "raw_message": [{"type": "at", "data": {"qq": "999"}}],
                "processed_plain_text": "https://www.bilibili.com/video/BV1TEST00001",
            }
        )
        self.assertEqual(len(self.appender.calls), 1)
        self.assertIn("深度读", self.appender.calls[0]["segments"][0]["content"])

    async def test_auto_read_brief_skips_vision(self) -> None:
        async def boom(brief):
            raise AssertionError("brief 模式不该抽帧")

        self.plugin._describe_video_frames = boom
        await self.plugin._maybe_auto_read(
            {
                "stream_id": "stream-1",
                "processed_plain_text": "https://www.bilibili.com/video/BV1TEST00001",
            }
        )
        self.assertEqual(len(self.appender.calls), 1)
        content = self.appender.calls[0]["segments"][0]["content"]
        self.assertIn("快速读", content)
        self.assertNotIn("画面描述", content)

    async def test_auto_read_brief_then_full_is_not_deduped(self) -> None:
        self.plugin._describe_video_frames = _no_vision
        plain = {
            "stream_id": "stream-1",
            "processed_plain_text": "https://www.bilibili.com/video/BV1TEST00001",
        }
        await self.plugin._maybe_auto_read(plain)
        deep = dict(plain)
        deep["processed_plain_text"] = "详细看看 https://www.bilibili.com/video/BV1TEST00001"
        await self.plugin._maybe_auto_read(deep)
        self.assertEqual(len(self.appender.calls), 2)


class FullReadTriggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_plugin_module()

    def test_mention_triggers_full(self) -> None:
        message = {"raw_message": [{"type": "at", "data": {"qq": "123"}}]}
        self.assertTrue(self.module.wants_full_read(message, "看看这个", []))

    def test_keyword_triggers_full(self) -> None:
        self.assertTrue(
            self.module.wants_full_read({}, "你详细看看这个视频", ["详细看看"])
        )

    def test_plain_text_stays_brief(self) -> None:
        self.assertFalse(self.module.wants_full_read({}, "看看这个", ["详细看看"]))


if __name__ == "__main__":
    unittest.main()
