"""学梗（bili_learn_meme）与黑话库写入的单元测试。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import FakeCtx, FakeDb, FakeLlm, load_plugin_module  # noqa: E402
from test_bili_read import _FakeReadClient, sample_bundle  # noqa: E402


def meme_json(**overrides):
    data = {
        "is_meme": True,
        "content": "退退退",
        "aliases": ["退钱"],
        "origin": "主播名场面",
        "meaning": "表达强烈要求后退或退钱的戏谑说法",
        "usage": "被人怼的时候喊",
        "examples": ["你别过来啊"],
        "freshness": "active",
        "taboo": {"level": "none", "reason": ""},
        "confidence": 0.82,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class _FakeAppender:
    def __init__(self) -> None:
        self.calls = []

    async def append(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True}


def meme_bundle(title="梗指南：退退退是什么梗"):
    bundle = sample_bundle()
    bundle["view"] = dict(bundle["view"], title=title)
    return bundle


class LearnMemeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)
        self.module = load_plugin_module()
        self.appender = _FakeAppender()
        self.llm = FakeLlm(judge_response=meme_json())
        self.db = FakeDb()
        self.ctx = FakeCtx(self.tmp_dir, llm=self.llm, db=self.db)
        self.ctx.maisaka = SimpleNamespace(context=self.appender)
        self.client = _FakeReadClient(meme_bundle())
        self.plugin = self._make_plugin()

    def _make_plugin(self, **overrides):
        config = {
            "plugin": {"enabled": True, "config_version": "1.4.0"},
            "interest": {"llm_task_name": "utils"},
            "read": {
                "enabled": True,
                "auto_read_enabled": True,
                "comment_limit": 2,
                "danmaku_limit": 3,
                "cache_seconds": 900,
            },
            "meme": {"enabled": True, "write_to_db": True, "auto_learn": True},
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
        plugin._judge = self.module.LlmJudge(self.llm, task_name="utils")
        return plugin

    def test_component_registered_as_visible_core_tool(self) -> None:
        components = self.plugin.get_components()
        tools = [item for item in components if item.get("name") == "bili_learn_meme"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].get("chat_scope"), "all")
        metadata = tools[0].get("metadata") or {}
        self.assertEqual(metadata.get("visibility"), "visible")
        self.assertIs(metadata.get("core_tool"), True)
        self.assertIn("子代理", metadata.get("description", ""))

    async def test_tool_saves_new_meme(self) -> None:
        result = await self.plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertTrue(result["success"], result)
        self.assertIn("学到了梗：退退退", result["content"])
        self.assertIn("已写入黑话库", result["content"])
        kinds = [call["query_type"] for call in self.db.calls]
        self.assertEqual(kinds, ["get", "create"])
        self.assertEqual(self.db.calls[0]["filters"], {"content": "退退退"})
        payload = self.db.calls[1]["data"]
        self.assertEqual(payload["content"], "退退退")
        self.assertIs(payload["is_jargon"], True)
        self.assertIs(payload["is_global"], True)
        self.assertEqual(payload["created_by"], "AI")
        self.assertIn("用法：被人怼的时候喊", payload["meaning"])
        self.assertIn('"bilibili"', payload["evidence_messages"])
        self.assertIn("BV1TEST00001", payload["evidence_messages"])

    async def test_tool_requires_switch_on(self) -> None:
        plugin = self._make_plugin(meme={"enabled": False})
        result = await plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertFalse(result["success"])
        self.assertIn("学梗功能没开", result["error"])
        self.assertEqual(self.db.calls, [])

    async def test_tool_rejects_low_confidence(self) -> None:
        self.llm.judge_response = meme_json(confidence=0.1)
        result = await self.plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertFalse(result["success"])
        self.assertIn("置信度", result["error"])
        self.assertEqual(self.db.calls, [])

    async def test_tool_rejects_strong_taboo(self) -> None:
        self.llm.judge_response = meme_json(taboo={"level": "strong", "reason": "地域攻击"})
        result = await self.plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertFalse(result["success"])
        self.assertIn("高敏感", result["error"])
        self.assertEqual(self.db.calls, [])

    async def test_tool_rejects_non_meme(self) -> None:
        self.llm.judge_response = meme_json(is_meme=False)
        result = await self.plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertFalse(result["success"])
        self.assertIn("没有可收录的梗", result["error"])

    async def test_tool_rejects_broken_json(self) -> None:
        self.llm.judge_response = "我觉得这个视频挺好看的"
        result = await self.plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertFalse(result["success"])
        self.assertIn("不是 JSON", result["error"])

    async def test_existing_ai_meme_is_updated(self) -> None:
        self.db.rows = [
            {
                "id": 7,
                "content": "退退退",
                "created_by": "AI",
                "evidence_messages": '[[{"platform": "bilibili", "message_id": "BV1OLD"}]]',
            }
        ]
        result = await self.plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertTrue(result["success"], result)
        self.assertIn("已更新", result["content"])
        kinds = [call["query_type"] for call in self.db.calls]
        self.assertEqual(kinds, ["get", "update"])
        update = self.db.calls[1]
        self.assertEqual(update["filters"], {"id": 7})
        self.assertIn("BV1TEST00001", update["data"]["evidence_messages"])

    async def test_existing_manual_meme_is_preserved(self) -> None:
        self.db.rows = [
            {"id": 7, "content": "退退退", "created_by": "USER", "evidence_messages": "[]"}
        ]
        result = await self.plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertTrue(result["success"], result)
        self.assertIn("保留原记录", result["content"])
        self.assertEqual([call["query_type"] for call in self.db.calls], ["get"])

    async def test_write_to_db_off_keeps_card_only(self) -> None:
        plugin = self._make_plugin(meme={"write_to_db": False})
        result = await plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertTrue(result["success"], result)
        self.assertIn("学到了梗：退退退", result["content"])
        self.assertNotIn("已写入黑话库", result["content"])
        self.assertEqual(self.db.calls, [])

    async def test_missing_db_capability_degrades(self) -> None:
        self.ctx.db = None
        result = await self.plugin.tool_bili_learn_meme(url="BV1TEST00001")
        self.assertTrue(result["success"], result)
        self.assertIn("只给你梗卡", result["content"])
        self.assertIn("database.query", result["content"])

    async def test_auto_learn_only_for_meme_titles(self) -> None:
        brief, _text = await self.plugin.read_video("BV1TEST00001", detail="full")
        self.assertTrue(self.plugin._should_learn_meme(brief))
        plain = self._make_plugin()
        plain._client = _FakeReadClient(sample_bundle())
        plain_brief, _t = await plain.read_video("BV1TEST00001", detail="full")
        self.assertFalse(plain._should_learn_meme(plain_brief))

    async def test_auto_learn_skips_when_switch_off(self) -> None:
        plugin = self._make_plugin(meme={"auto_learn": False})
        brief, _text = await plugin.read_video("BV1TEST00001", detail="full")
        self.assertFalse(plugin._should_learn_meme(brief))

    async def test_auto_learn_writes_once_and_notes(self) -> None:
        brief, _text = await self.plugin.read_video("BV1TEST00001", detail="full")
        await self.plugin._auto_learn_meme(brief, "stream-1")
        self.assertEqual([call["query_type"] for call in self.db.calls], ["get", "create"])
        self.assertEqual(len(self.appender.calls), 1)
        self.assertEqual(self.appender.calls[0]["stream_id"], "stream-1")
        self.assertEqual(self.appender.calls[0]["visible_text"], "学到一个梗")
        self.assertIn("退退退", self.appender.calls[0]["segments"][0]["content"])
        self.assertFalse(self.plugin._should_learn_meme(brief), "同一个视频只学一次")

    async def test_auto_read_triggers_learning_for_meme_video(self) -> None:
        await self.plugin._maybe_auto_read(
            {
                "stream_id": "stream-1",
                "processed_plain_text": "https://www.bilibili.com/video/BV1TEST00001",
            }
        )
        self.assertEqual(len(self.appender.calls), 2, "先塞视频上下文，再塞学梗说明")
        self.assertIn("【B站视频】", self.appender.calls[0]["segments"][0]["content"])
        self.assertEqual(self.appender.calls[1]["visible_text"], "学到一个梗")
        self.assertEqual([call["query_type"] for call in self.db.calls], ["get", "create"])


if __name__ == "__main__":
    unittest.main()
