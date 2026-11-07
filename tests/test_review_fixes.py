"""审核反馈修复的回归测试：类别上限、指令白名单、赤石屏蔽词、指令参数解析。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import (  # noqa: E402
    FakeClient,
    FakeCtx,
    FakeLlm,
    load_plugin_module,
    load_submodule,
    make_video,
)
from test_plugin import JUDGE_OK, base_config  # noqa: E402


class ReviewFixesTestCase(unittest.IsolatedAsyncioTestCase):
    """把审核提的几条边角问题各钉一个测试，防止以后改回去。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.module = load_plugin_module()
        self.video_module = load_submodule("bilibili")
        self.ctx = FakeCtx(self.tmp_dir)

    def make_plugin(self, config=None, *, videos=None, ctx=None, client=None):
        ctx = ctx or self.ctx
        plugin = self.module.BilibiliInterestSharePlugin()
        plugin._set_context(ctx)
        plugin.set_plugin_config(config or base_config())
        if client is not None:
            plugin._client = client
        else:
            plugin._client = FakeClient(
                videos
                if videos is not None
                else [make_video(self.video_module, bvid="BV1", title="原神新版本测评")]
            )
        plugin._store = self.module.SeenStore(
            self.tmp_dir / "state.json", dedupe_days=30, max_records=1000
        )
        plugin._store.load()
        plugin._data_dir = self.tmp_dir
        config_obj = plugin.config
        persona_module = load_submodule("persona")
        plugin._persona = persona_module.PersonaProvider(
            enabled=config_obj.persona.enabled,
            config_get=ctx.config.get,
            override_name=config_obj.persona.name,
            override_personality=config_obj.persona.personality,
            override_reply_style=config_obj.persona.reply_style,
            style_hint=config_obj.persona.style_hint,
            cache_seconds=config_obj.persona.cache_seconds,
        )
        plugin._judge = self.module.LlmJudge(
            ctx.llm,
            task_name=config_obj.interest.llm_task_name,
            vision_task_name=config_obj.vision.task_name,
            temperature=config_obj.interest.llm_temperature,
            persona_provider=plugin._persona,
        )
        plugin._frames = None
        return plugin

    def _category_config(self, cap: int):
        return base_config(
            categories=[
                {
                    "name": "社区创作",
                    "enabled": True,
                    "keywords": ["原神"],
                    "search_order": "",
                    "max_share_per_round": cap,
                    "min_play": 0,
                }
            ],
            interest={"use_llm_judge": True},
            schedule={"max_share_per_round": 3},
        )

    def _three_videos(self):
        return [
            make_video(self.video_module, bvid=f"BVC{index}", title=f"原神二创第{index}期")
            for index in range(1, 4)
        ]

    # -- 类别级每轮上限 -------------------------------------------------

    async def test_category_cap_limits_shares(self) -> None:
        """[[categories]].max_share_per_round 真的生效：同一类别最多分享 1 个。"""

        ctx = FakeCtx(self.tmp_dir, llm=FakeLlm(judge_response=JUDGE_OK))
        plugin = self.make_plugin(
            self._category_config(1), videos=self._three_videos(), ctx=ctx
        )
        summary = await plugin.run_round(reason="test")
        self.assertEqual(summary["shared"], 1)

    async def test_no_category_cap_shares_all(self) -> None:
        """不设类别上限时同一轮可以把名额用满，作为上一条的对照。"""

        ctx = FakeCtx(self.tmp_dir, llm=FakeLlm(judge_response=JUDGE_OK))
        plugin = self.make_plugin(
            self._category_config(0), videos=self._three_videos(), ctx=ctx
        )
        summary = await plugin.run_round(reason="test")
        self.assertEqual(summary["shared"], 3)

    # -- 工具走白名单 ---------------------------------------------------

    async def test_search_tool_rejects_non_whitelisted_stream(self) -> None:
        plugin = self.make_plugin(
            client=FakeClient(
                videos=[], search_results=[make_video(self.video_module, bvid="BVS9")]
            )
        )
        result = await plugin.tool_bili_search(stream_id="other-stream", query="原神")
        self.assertFalse(result["success"])
        self.assertIn("白名单", result["error"])
        joined = "\n".join(item["text"] for item in self.ctx.send.messages)
        self.assertNotIn("bilibili.com/video", joined)

    async def test_shiwei_tool_rejects_non_whitelisted_stream(self) -> None:
        plugin = self.make_plugin(
            client=FakeClient(
                videos=[],
                search_results=[
                    make_video(self.video_module, bvid="BVSHI9", title="离谱整活：抽象操作")
                ],
            )
        )
        result = await plugin.tool_bili_shiwei(stream_id="other-stream")
        self.assertFalse(result["success"])
        self.assertIn("白名单", result["error"])
        joined = "\n".join(item["text"] for item in self.ctx.send.messages)
        self.assertNotIn("bilibili.com/video", joined)

    # -- 赤石路径的全局屏蔽词 -------------------------------------------

    async def test_shiwei_respects_global_block_keywords(self) -> None:
        """赤石视频也要过 [filter].block_keywords，别把广告当整活发出去。"""

        plugin = self.make_plugin(
            base_config(filter={"block_keywords": ["广告"]}),
            client=FakeClient(
                videos=[],
                search_results=[
                    make_video(
                        self.video_module,
                        bvid="BVAD",
                        title="离谱整活：这广告也太抽象了",
                        play=200000,
                    )
                ],
            ),
        )
        result = await plugin.share_shiwei("stream-1")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_candidates")
        self.assertEqual(self.ctx.send.messages, [])

    # -- 工具参数校验 ---------------------------------------------------

    async def test_search_tool_without_query_is_rejected(self) -> None:
        """没给关键词时不能拿空串去搜 B 站。"""

        plugin = self.make_plugin(
            client=FakeClient(
                videos=[], search_results=[make_video(self.video_module, bvid="BVS8")]
            )
        )
        result = await plugin.tool_bili_search(stream_id="stream-1", query="")
        self.assertFalse(result["success"])
        self.assertIn("缺少搜索关键词", result["error"])
        self.assertEqual(self.ctx.send.messages, [])

    async def test_search_tool_passes_query_through(self) -> None:
        plugin = self.make_plugin(
            client=FakeClient(
                videos=[], search_results=[make_video(self.video_module, bvid="BVS7")]
            )
        )
        result = await plugin.tool_bili_search(stream_id="stream-1", query="机械键盘")
        self.assertTrue(result["success"], result)
        self.assertIn("机械键盘", result["content"])


if __name__ == "__main__":
    unittest.main()
