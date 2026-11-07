"""学梗纯逻辑（meme.py）的单元测试。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import load_submodule  # noqa: E402

MEME = load_submodule("meme")


def meme_payload(**overrides):
    data = {
        "is_meme": True,
        "content": "退退退",
        "meaning": "表达强烈要求后退或退钱的戏谑说法",
        "usage": "被人怼的时候喊",
        "examples": ["你别过来啊"],
        "aliases": ["退钱"],
        "origin": "主播名场面",
        "freshness": "active",
        "taboo": {"level": "none", "reason": ""},
        "confidence": 0.82,
    }
    data.update(overrides)
    return data


class CleanDanmakuTests(unittest.TestCase):
    def test_drops_short_and_emotional_danmaku(self) -> None:
        items = ["哈哈哈", "666", "这波操作太秀了吧", "绷不住", "这波操作太秀了吧"]
        self.assertEqual(MEME.clean_danmaku(items), ["这波操作太秀了吧"])

    def test_keeps_long_danmaku_even_if_it_contains_junk(self) -> None:
        text = "哈哈哈哈这个操作我真是绷不住了"
        self.assertEqual(MEME.clean_danmaku([text]), [text])

    def test_limits_and_handles_empty(self) -> None:
        items = ["第一个有用的弹幕呀", "第二个有用的弹幕呀", "第三个有用的弹幕呀"]
        self.assertEqual(len(MEME.clean_danmaku(items, limit=2)), 2)
        self.assertEqual(MEME.clean_danmaku([]), [])


class DanmakuBurstTests(unittest.TestCase):
    def test_picks_biggest_buckets_and_sorts_by_time(self) -> None:
        items = [
            (3, "开场第一条"),
            (5, "开场第二条"),
            (95, "高能第一条"),
            (97, "高能第二条"),
            (99, "高能第三条"),
            (205, "收尾第一条"),
        ]
        bursts = MEME.danmaku_bursts(items, window_seconds=10, top=2, samples=2)
        self.assertEqual([item["start"] for item in bursts], [0, 90])
        self.assertEqual(bursts[1]["count"], 3)
        self.assertEqual(bursts[1]["samples"], ["高能第一条", "高能第二条"])

    def test_empty_and_malformed_entries(self) -> None:
        self.assertEqual(MEME.danmaku_bursts([]), [])
        self.assertEqual(MEME.danmaku_bursts([("x", ""), (1, 2)])[0]["count"], 1)


class SelectCommentTests(unittest.TestCase):
    def test_prefers_comment_that_explains_the_meme(self) -> None:
        comments = [
            ("哈哈哈哈哈哈笑死我了", "路人甲", 0),
            ("这个梗的起源我来讲一下，其实是 2018 年的名场面", "课代表", 5),
        ]
        picked = MEME.select_comments(comments, limit=5)
        self.assertEqual(picked[0][1], "课代表")
        self.assertEqual(len(picked), 2)

    def test_drops_short_and_respects_limit(self) -> None:
        self.assertEqual(MEME.select_comments([("太短", "a", 0)]), [])
        many = [("这条评论足够长了吧%d" % index, "b", index) for index in range(6)]
        self.assertEqual(len(MEME.select_comments(many, limit=3)), 3)


class ScoreSourceTests(unittest.TestCase):
    def test_meme_title_subtitle_and_good_comments_raise_score(self) -> None:
        plain = MEME.score_source(title="原神新版本测评", play=1000, danmaku=100)
        rich = MEME.score_source(
            title="梗指南：原神新版本测评",
            play=1000,
            danmaku=100,
            subtitle_chars=500,
            comments=["讲得全面"],
        )
        self.assertGreater(rich, plain + 2.0)

    def test_bad_comments_lower_score(self) -> None:
        good = MEME.score_source(title="随便一个标题", comments=["谢谢科普"])
        bad = MEME.score_source(title="随便一个标题", comments=["瞎讲"])
        self.assertGreater(good, bad)


class BuildPromptTests(unittest.TestCase):
    def test_prompt_has_materials_rules_and_no_duplicated_header(self) -> None:
        prompt = MEME.build_meme_prompt(
            title="梗指南：退退退是什么梗",
            owner="测试UP",
            partition="鬼畜",
            duration=125,
            tags=["鬼畜", "梗"],
            play=120000,
            danmaku=800,
            description="本期讲退退退",
            subtitle="他直接退退退了三步",
            bursts=[{"start": 90, "count": 3, "samples": ["退退退"]}],
            comments=[("这个梗起源于主播名场面呢", "课代表", 3)],
            term_hint="退退退",
        )
        self.assertEqual(prompt[0]["role"], "user")
        content = prompt[0]["content"]
        self.assertEqual(content.count("标题："), 1)
        self.assertIn("[01:30] 3 条", content)
        self.assertIn("【热评】", content)
        self.assertIn("【字幕节选】", content)
        self.assertIn("用户点名的梗：退退退", content)
        self.assertIn("原句", content)
        self.assertIn("is_meme", content)

    def test_prompt_omits_empty_sections(self) -> None:
        content = MEME.build_meme_prompt(title="没有材料")[0]["content"]
        self.assertNotIn("【字幕节选】", content)
        self.assertNotIn("【弹幕爆发点】", content)


class ParseMemeJsonTests(unittest.TestCase):
    def test_parses_plain_and_fenced_json(self) -> None:
        self.assertEqual(MEME.parse_meme_json('{"is_meme": true}'), {"is_meme": True})
        fenced = "说明一下：\n```json\n{\"is_meme\": false}\n```\n完"
        self.assertEqual(MEME.parse_meme_json(fenced), {"is_meme": False})

    def test_returns_none_for_garbage(self) -> None:
        self.assertIsNone(MEME.parse_meme_json(""))
        self.assertIsNone(MEME.parse_meme_json("我觉得这不是梗"))
        self.assertIsNone(MEME.parse_meme_json("{不是 json}"))
        self.assertIsNone(MEME.parse_meme_json("[1, 2, 3]"))


class NormalizeMemeTests(unittest.TestCase):
    def test_accepts_usable_meme(self) -> None:
        record, reason = MEME.normalize_meme(meme_payload())
        self.assertEqual(reason, "")
        assert record is not None
        self.assertEqual(record["content"], "退退退")
        self.assertEqual(record["freshness"], "active")
        self.assertEqual(record["taboo_level"], "none")
        self.assertEqual(record["confidence"], 0.82)
        self.assertIn("用法：被人怼的时候喊", record["meaning_text"])

    def test_rejects_not_a_meme(self) -> None:
        record, reason = MEME.normalize_meme(meme_payload(is_meme=False))
        self.assertIsNone(record)
        self.assertIn("没有可收录的梗", reason)

    def test_rejects_missing_or_too_long_content(self) -> None:
        self.assertIn("梗名", MEME.normalize_meme(meme_payload(content=""))[1])
        long_name = "梗" * 300
        self.assertIn("超过", MEME.normalize_meme(meme_payload(content=long_name))[1])

    def test_rejects_strong_taboo(self) -> None:
        payload = meme_payload(taboo={"level": "strong", "reason": "地域攻击"})
        record, reason = MEME.normalize_meme(payload)
        self.assertIsNone(record)
        self.assertIn("高敏感", reason)
        self.assertIn("地域攻击", reason)

    def test_keeps_strong_taboo_when_switch_off(self) -> None:
        payload = meme_payload(taboo={"level": "strong", "reason": "地域攻击"})
        record, reason = MEME.normalize_meme(payload, reject_taboo_strong=False)
        self.assertEqual(reason, "")
        self.assertEqual(record["taboo_level"], "strong")

    def test_rejects_low_confidence(self) -> None:
        record, reason = MEME.normalize_meme(meme_payload(confidence=0.1))
        self.assertIsNone(record)
        self.assertIn("置信度", reason)

    def test_rejects_empty_meaning(self) -> None:
        self.assertIn("没给出含义", MEME.normalize_meme(meme_payload(meaning=""))[1])

    def test_normalizes_unknown_freshness(self) -> None:
        record, _reason = MEME.normalize_meme(meme_payload(freshness="远古"))
        self.assertEqual(record["freshness"], "active")


class ComposeAndCardTests(unittest.TestCase):
    def test_compose_meaning_joins_parts(self) -> None:
        record, _reason = MEME.normalize_meme(meme_payload())
        text = record["meaning_text"]
        self.assertIn("用法：", text)
        self.assertIn("例句：", text)
        self.assertIn("来历：", text)
        self.assertIn("别名：", text)
        self.assertIn("时效：active", text)
        self.assertLessEqual(len(text), MEME.MAX_MEANING_CHARS)

    def test_format_card_contains_key_fields(self) -> None:
        record, _reason = MEME.normalize_meme(meme_payload())
        card = MEME.format_meme_card(
            record, video_title="梗指南", url="https://www.bilibili.com/video/BV1", saved="（已写入黑话库）"
        )
        self.assertTrue(card.startswith("学到了梗：退退退"))
        self.assertIn("含义：", card)
        self.assertIn("置信 0.82", card)
        self.assertIn("来源视频：梗指南", card)
        self.assertIn("已写入黑话库", card)


if __name__ == "__main__":
    unittest.main()
