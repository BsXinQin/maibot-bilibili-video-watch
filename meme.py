"""学梗（bili_learn_meme）的纯逻辑。

把一条 B 站视频的材料（字幕 / 弹幕爆发点 / 热评 / 标签）整理成提示词，
再把模型吐出来的梗卡校验成可以写进麦麦黑话库的记录。

这一层不碰网络、不碰宿主能力，全部是纯函数，方便单测。
"""

from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 黑话库 Jargon.content 的长度上限（宿主模型里是 String(255)）
MAX_CONTENT_CHARS = 255
#: 写进 Jargon.meaning 的文本上限（宿主是 Text，但太长没意义）
MAX_MEANING_CHARS = 1200
#: 一条评论截断长度
MAX_LINE_CHARS = 200

#: 纯情绪、没有信息量的弹幕：学梗用不上，还占提示词
JUNK_DANMAKU = (
    "哈哈哈",
    "233",
    "666",
    "已阅",
    "打卡",
    "前方高能",
    "第一",
    "来了",
    "好听",
    "笑死",
    "绷不住",
    "泪目",
    "awsl",
    "沙发",
    "顶",
    "路过",
)

#: 评论里出现这些词，说明这条评论在解释梗（有价值）
POSITIVE_COMMENT_HINTS = (
    "讲得全面",
    "起源",
    "捋清楚",
    "课代表",
    "省流",
    "科普",
    "详细",
    "谢谢",
    "受教",
    "了解了",
    "原来",
    "懂了",
    "涨知识",
)
#: 评论里出现这些词，说明这条评论在纠错（也有价值：能看出哪里讲错了）
NEGATIVE_COMMENT_HINTS = ("瞎讲", "不对", "错的", "胡说", "误导", "扯淡", "编的", "离谱")
#: 标题里出现这些词，基本可以确定是讲梗的视频
MEME_TITLE_HINTS = ("什么梗", "梗指南", "是什么梗", "梗科普", "梗百科", "梗来源")

FRESHNESS_VALUES = ("active", "fading", "dead")
TABOO_LEVELS = ("none", "mild", "strong")


def _text(value: Any, limit: int = 0) -> str:
    """把任意值转成去空白的字符串，可按长度截断。"""

    result = str(value or "").strip()
    if limit > 0 and len(result) > limit:
        result = result[: max(1, limit - 1)].rstrip() + "…"
    return result


def _has_letters(text: str) -> bool:
    """有汉字，或者有两个连续字母 / 数字（用来过滤「!!」「。。」这种）。"""

    return bool(re.search(r"[\u4e00-\u9fff]", text)) or bool(re.search(r"[0-9A-Za-z]{2,}", text))


def clean_danmaku(
    items: Sequence[Any], *, min_length: int = 4, limit: int = 60
) -> List[str]:
    """过滤纯情绪弹幕与过短的弹幕，保留有信息量的样本（去重、保序）。"""

    result: List[str] = []
    seen: List[str] = []
    floor = max(1, int(min_length))
    for item in items or ():
        text = _text(item, 60)
        if len(text) < floor:
            continue
        if not _has_letters(text):
            continue
        lowered = text.lower()
        if len(text) <= 6 and any(junk in lowered for junk in JUNK_DANMAKU):
            continue
        if text in seen:
            continue
        seen.append(text)
        result.append(text)
        if limit and len(result) >= int(limit):
            break
    return result


def danmaku_bursts(
    items: Sequence[Tuple[int, str]],
    *,
    window_seconds: int = 10,
    top: int = 4,
    samples: int = 4,
) -> List[Dict[str, Any]]:
    """按 N 秒分桶找弹幕峰值。

    弹幕在同一段里炸开的地方，通常就是视频里的梗 / 名场面。
    返回按时间排序的若干段：``{"start": 起始秒, "count": 条数, "samples": [代表弹幕]}``。
    """

    window = max(1, int(window_seconds))
    buckets: Dict[int, List[Tuple[int, str]]] = {}
    for entry in items or ():
        try:
            start, text = entry
        except (TypeError, ValueError):
            continue
        try:
            seconds = max(0, int(start))
        except (TypeError, ValueError):
            seconds = 0
        text = _text(text, 60)
        if not text:
            continue
        buckets.setdefault(seconds // window, []).append((seconds, text))
    ordered = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    chosen: List[Dict[str, Any]] = []
    for number, entries in ordered[: max(1, int(top))]:
        entries.sort(key=lambda pair: pair[0])
        chosen.append(
            {
                "start": number * window,
                "count": len(entries),
                "samples": [text for _seconds, text in entries[: max(1, int(samples))]],
            }
        )
    chosen.sort(key=lambda item: item["start"])
    return chosen


def select_comments(
    comments: Sequence[Tuple[str, str, int]], *, limit: int = 14
) -> List[Tuple[str, str, int]]:
    """挑评论：长度 + 点赞 + 「在解释梗 / 在纠错」的信号，权重高的优先。"""

    scored: List[Tuple[float, Tuple[str, str, int]]] = []
    for entry in comments or ():
        try:
            text, user, like = entry
        except (TypeError, ValueError):
            continue
        text = _text(text, MAX_LINE_CHARS)
        if len(text) < 8:
            continue
        try:
            likes = max(0, int(like or 0))
        except (TypeError, ValueError):
            likes = 0
        weight = len(text) * 0.02 + min(3.0, math.sqrt(likes) * 0.15)
        if any(hint in text for hint in POSITIVE_COMMENT_HINTS):
            weight += 1.5
        if any(hint in text for hint in NEGATIVE_COMMENT_HINTS):
            weight += 0.8
        scored.append((weight, (text, _text(user, 24), likes)))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [item for _weight, item in scored[: max(0, int(limit))]]

def score_source(
    *,
    title: str = "",
    play: int = 0,
    danmaku: int = 0,
    subtitle_chars: int = 0,
    comments: Sequence[Any] = (),
) -> float:
    """候选视频「值不值得花一次 LLM 去学」的打分（多候选选源用）。"""

    text = str(title or "")
    danmaku_count = _int(danmaku)
    play_count = _int(play)
    score = 0.6 * math.log1p(danmaku_count) + 0.25 * math.log1p(play_count)
    if any(hint in text for hint in MEME_TITLE_HINTS):
        score += 2.0
    if "梗" in text:
        score += 0.5
    body = " ".join(str(item) for item in comments or ())
    positives = sum(1 for hint in POSITIVE_COMMENT_HINTS if hint in body)
    negatives = sum(1 for hint in NEGATIVE_COMMENT_HINTS if hint in body)
    score += 0.35 * positives - 0.8 * negatives
    if _int(subtitle_chars) >= 200:
        score += 3.0
    return round(score, 3)


def _int(value: Any) -> int:
    """安全转 int（None / 字符串 / 浮点都吃）。"""

    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _mmss(seconds: int) -> str:
    """把秒数格式化成 mm:ss。"""

    total = _int(seconds)
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def build_meme_prompt(
    *,
    title: str,
    owner: str = "",
    partition: str = "",
    duration: int = 0,
    tags: Sequence[str] = (),
    play: int = 0,
    danmaku: int = 0,
    description: str = "",
    subtitle: str = "",
    bursts: Sequence[Dict[str, Any]] = (),
    comments: Sequence[Sequence[Any]] = (),
    term_hint: str = "",
    max_subtitle_chars: int = 6000,
    max_description_chars: int = 600,
) -> List[Dict[str, str]]:
    """拼「从视频里学梗」的提示词（消息列表格式，直接喂给 llm.generate）。"""

    head: List[str] = ["标题：" + _text(title, 120)]
    row: List[str] = []
    if owner:
        row.append("UP主：" + _text(owner, 30))
    if partition:
        row.append("分区：" + _text(partition, 20))
    if duration:
        row.append("时长：%d 秒" % _int(duration))
    if row:
        head.append(" ｜ ".join(row))
    head.append("播放 %d ｜ 弹幕 %d" % (_int(play), _int(danmaku)))
    if tags:
        head.append("标签：" + " / ".join(_text(item, 16) for item in tags[:10]))
    if description:
        head.append("简介：" + _text(description, max_description_chars))
    if term_hint:
        head.append("用户点名的梗：" + _text(term_hint, 60))

    body: List[str] = []
    if bursts:
        body.append("")
        body.append("【弹幕爆发点】（条数多的区间，往往是梗 / 名场面出现的地方）")
        for burst in bursts:
            samples = " / ".join(str(item) for item in burst.get("samples") or ())
            start = _int(burst.get("start"))
            count = _int(burst.get("count"))
            body.append("[" + _mmss(start) + "] " + str(count) + " 条：" + samples)
    if comments:
        body.append("")
        body.append("【热评】（能看出观众觉得这段在讲什么）")
        for index, entry in enumerate(list(comments)[:20], start=1):
            try:
                text, user, like = entry
            except (TypeError, ValueError):
                continue
            who = (_text(user, 20) + "：") if user else ""
            body.append("%d. （%d 赞）%s%s" % (index, _int(like), who, _text(text, 160)))
    if subtitle:
        body.append("")
        body.append("【字幕节选】（只有这里能作为「来历」的依据）")
        body.append(_text(subtitle, max_subtitle_chars))

    rules = [
        "",
        "【任务】",
        "判断这段材料里有没有一个具体可用的「梗」（网络流行语 / 名场面台词 / 鬼畜素材 / 圈内黑话）。",
        "有就整理成一张梗卡；没有就把 is_meme 设为 false。",
        "",
        "【硬规则】",
        "1. origin（来历）只能来自上面的字幕；没有字幕就留空字符串，绝对不许自己编。",
        "2. examples（例句）必须是材料里出现过的原句，不许自己造。",
        "3. 材料不足以说清含义时，把 confidence 压到 0.3 以下。",
        "4. 不要把 UP 主名字、视频标题、分区名当梗。",
        "5. taboo（敏感度）：涉及地域 / 民族 / 性别 / 身体缺陷 / 灾难 / 逝者的梗，一律不许是 none。",
        "6. 只输出一个 JSON 对象，不要开场白、不要解释、不要代码块标记。",
        "",
        "【输出格式】",
        "{",
        '  "is_meme": true,',
        '  "content": "梗名（2~10 字）",',
        '  "aliases": ["别名"],',
        '  "origin": "来历（来自字幕，没有就空串）",',
        '  "meaning": "含义（30~120 字，说清为什么好笑 / 什么时候用）",',
        '  "usage": "怎么用（一句话）",',
        '  "examples": ["材料里的原句"],',
        '  "freshness": "active | fading | dead",',
        '  "taboo": {"level": "none | mild | strong", "reason": ""},',
        '  "confidence": 0.0',
        "}",
    ]
    content = "\n".join(head + body + rules)
    return [{"role": "user", "content": content}]


def parse_meme_json(text: Any) -> Optional[Dict[str, Any]]:
    """从模型输出里抠出 JSON 对象（容忍代码块和前后废话）。"""

    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _string_list(value: Any, limit: int, item_chars: int = 60) -> List[str]:
    """把模型给的别名 / 例句整理成字符串列表（去重、限量）。"""

    if isinstance(value, str):
        items: List[Any] = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    result: List[str] = []
    for item in items:
        text = _text(item, item_chars)
        if text and text not in result:
            result.append(text)
        if len(result) >= max(0, int(limit)):
            break
    return result


def compose_meaning(record: Dict[str, Any]) -> str:
    """把含义 / 用法 / 例句 / 来历揉成黑话库的 meaning 字段（那是唯一的自由文本位）。"""

    parts: List[str] = [_text(record.get("meaning"), 600)]
    usage = _text(record.get("usage"), 200)
    if usage:
        parts.append("用法：" + usage)
    examples = record.get("examples") or []
    if examples:
        parts.append("例句：" + " / ".join(str(item) for item in examples[:2]))
    origin = _text(record.get("origin"), 200)
    if origin:
        parts.append("来历：" + origin)
    aliases = record.get("aliases") or []
    if aliases:
        parts.append("别名：" + "、".join(str(item) for item in aliases[:4]))
    freshness = _text(record.get("freshness"), 10)
    if freshness:
        parts.append("时效：" + freshness)
    text = " ｜ ".join(part for part in parts if part)
    return text[:MAX_MEANING_CHARS]


def normalize_meme(
    data: Dict[str, Any],
    *,
    min_confidence: float = 0.35,
    reject_taboo_strong: bool = True,
    max_content_chars: int = MAX_CONTENT_CHARS,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """校验模型输出。

    Returns:
        ``(record, "")`` 表示可以入库；``(None, 原因)`` 表示这次不收。
        record 里额外带了 ``meaning_text``（写库用的合并文本）。
    """

    if not isinstance(data, dict):
        return None, "模型输出不是 JSON 对象"
    if not data.get("is_meme"):
        return None, "判断这段材料里没有可收录的梗"
    raw_content = _text(data.get("content"))
    if not raw_content:
        return None, "模型没给出梗名"
    if len(raw_content) > max(1, int(max_content_chars)):
        return None, "梗名超过 %d 字，没有写入黑话库" % int(max_content_chars)
    content = raw_content
    taboo = data.get("taboo") if isinstance(data.get("taboo"), dict) else {}
    level = str(taboo.get("level") or "none").strip().lower()
    if level not in TABOO_LEVELS:
        level = "none"
    if level == "strong" and reject_taboo_strong:
        reason = _text(taboo.get("reason"), 120) or "未说明"
        return None, "「%s」被判定为高敏感梗（%s），没有入库" % (content, reason)
    try:
        confidence = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    threshold = float(min_confidence or 0.0)
    if confidence < threshold:
        return None, "「%s」的置信度只有 %.2f（低于 %.2f），材料不足，没有入库" % (
            content,
            confidence,
            threshold,
        )
    meaning = _text(data.get("meaning"), 600)
    if not meaning:
        return None, "「%s」没给出含义，没有入库" % content
    freshness = str(data.get("freshness") or "").strip().lower()
    if freshness not in FRESHNESS_VALUES:
        freshness = "active"
    record: Dict[str, Any] = {
        "content": content,
        "aliases": _string_list(data.get("aliases"), 4, 20),
        "origin": _text(data.get("origin"), 200),
        "meaning": meaning,
        "usage": _text(data.get("usage"), 200),
        "examples": _string_list(data.get("examples"), 3, 80),
        "freshness": freshness,
        "taboo_level": level,
        "taboo_reason": _text(taboo.get("reason"), 120),
        "confidence": round(confidence, 3),
    }
    record["meaning_text"] = compose_meaning(record)
    return record, ""


def format_meme_card(
    record: Dict[str, Any], *, video_title: str = "", url: str = "", saved: str = ""
) -> str:
    """把梗卡渲染成给模型看的纯文本（它会转述给用户）。"""

    content = str(record.get("content") or "")
    lines = ["学到了梗：" + content]
    lines.append("含义：" + str(record.get("meaning") or ""))
    if record.get("usage"):
        lines.append("用法：" + str(record["usage"]))
    examples = record.get("examples") or []
    if examples:
        lines.append("例句：" + " ｜ ".join(str(item) for item in examples[:3]))
    aliases = record.get("aliases") or []
    if aliases:
        lines.append("别名：" + "、".join(str(item) for item in aliases[:4]))
    if record.get("origin"):
        lines.append("来历：" + str(record["origin"]))
    meta = [
        "置信 %.2f" % float(record.get("confidence") or 0),
        "时效 " + str(record.get("freshness") or "active"),
    ]
    level = str(record.get("taboo_level") or "none")
    if level != "none":
        meta.append("敏感度 " + level)
    lines.append("；".join(meta))
    if video_title:
        source = "来源视频：" + str(video_title)
        if url:
            source += "（" + str(url) + "）"
        lines.append(source)
    if saved:
        lines.append(str(saved))
    return "\n".join(lines)
