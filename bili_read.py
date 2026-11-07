"""B 站视频直读（bili_read）。

网页抓取只能拿到「壳」：标题、简介、分P、弹幕、热评、字幕其实都在
api.bilibili.com 的 JSON 接口里。这个模块负责把一条链接 / BV 号整理成
模型能直接读的结构化文本，并做短期缓存。

模块只依赖 bilibili 模块里的客户端与格式化函数，不碰宿主 API，便于单测。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

try:  # 作为插件包导入
    from .bilibili import BilibiliClient, BilibiliError, Video, count_text, duration_text
except ImportError:  # 顶层模块导入（宿主按非包方式加载）时的兜底
    from bilibili import BilibiliClient, BilibiliError, Video, count_text, duration_text

BV_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
AV_PATTERN = re.compile(r"(?:^|[^0-9A-Za-z])av(\d{1,12})", re.IGNORECASE)
URL_PATTERN = re.compile(r"https?://[^\s<>\"'，。）)\]】]+", re.IGNORECASE)
PAGE_PATTERN = re.compile(r"[?&]p=(\d{1,4})", re.IGNORECASE)

SHORT_HOSTS = ("b23.tv", "bili2233.cn")
MAX_LINKS = 3
MAX_DESC_CHARS = 300
MAX_DANMAKU_LINE = 24


class BiliReadError(RuntimeError):
    """读取 B 站视频失败（链接不合法、接口报错、视频不存在……）。"""


@dataclass
class VideoTarget:
    """一条待读取的视频引用。"""

    bvid: str = ""
    aid: int = 0
    page: int = 1
    short_url: str = ""
    original: str = ""

    @property
    def empty(self) -> bool:
        """既没有视频号也没有短链时返回 True。"""

        return not (self.bvid or self.aid or self.short_url)

    @property
    def key(self) -> str:
        """缓存键（带上分P，避免 P1 与 P2 混用）。"""

        if self.bvid:
            return f"{self.bvid}:p{self.page}"
        if self.aid:
            return f"av{self.aid}:p{self.page}"
        return self.short_url


def _page_of(text: str) -> int:
    """从链接里取分P序号（取不到就是 1）。"""

    match = PAGE_PATTERN.search(text or "")
    if not match:
        return 1
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 1


def _host_of(url: str) -> str:
    try:
        return url.split("//", 1)[1].split("/", 1)[0].lower()
    except IndexError:
        return ""


def parse_target(text: str) -> Optional[VideoTarget]:
    """从任意文本里解析视频引用（链接、短链、BV 号、av 号）。"""

    raw = (text or "").strip()
    if not raw:
        return None
    url_match = URL_PATTERN.search(raw)
    if url_match:
        url = url_match.group(0).rstrip("。，、）)】")
        host = _host_of(url)
        if any(name in host for name in SHORT_HOSTS):
            return VideoTarget(short_url=url, original=raw)
        if "bilibili.com" in host:
            page = _page_of(url)
            found = BV_PATTERN.search(url)
            if found:
                return VideoTarget(bvid=found.group(0), page=page, original=raw)
            found = AV_PATTERN.search(url)
            if found:
                return VideoTarget(aid=int(found.group(1)), page=page, original=raw)
    found = BV_PATTERN.search(raw)
    if found:
        return VideoTarget(bvid=found.group(0), page=_page_of(raw), original=raw)
    found = AV_PATTERN.search(raw)
    if found:
        return VideoTarget(aid=int(found.group(1)), page=_page_of(raw), original=raw)
    return None


def extract_targets(text: str, *, limit: int = MAX_LINKS) -> List[str]:
    """按出现顺序抽出文本里的视频引用（去重），返回可直接再解析的片段。"""

    raw = text or ""
    hits: List[Tuple[int, str]] = []
    for match in URL_PATTERN.finditer(raw):
        url = match.group(0)
        lowered = url.lower()
        if "bilibili.com" in lowered or any(name in lowered for name in SHORT_HOSTS):
            hits.append((match.start(), url.rstrip("。，、）)】")))
    for match in BV_PATTERN.finditer(raw):
        hits.append((match.start(), match.group(0)))
    hits.sort(key=lambda item: item[0])
    targets: List[str] = []
    seen: Set[str] = set()
    for _, item in hits:
        target = parse_target(item)
        if target is None or target.empty:
            continue
        if target.key in seen:
            continue
        seen.add(target.key)
        targets.append(item)
        if len(targets) >= max(1, int(limit)):
            break
    return targets


@dataclass
class VideoBrief:
    """一条视频整理完的资料。"""

    bvid: str = ""
    aid: int = 0
    title: str = ""
    owner: str = ""
    partition: str = ""
    duration: int = 0
    pubdate: int = 0
    description: str = ""
    tags: List[str] = field(default_factory=list)
    pages: List[Tuple[int, str, int]] = field(default_factory=list)
    page: int = 1
    stats: Dict[str, int] = field(default_factory=dict)
    danmaku: List[str] = field(default_factory=list)
    comments: List[Tuple[str, str, int]] = field(default_factory=list)
    subtitle: str = ""
    subtitle_segments: List[Tuple[int, str]] = field(default_factory=list)
    danmaku_timeline: List[Tuple[int, str]] = field(default_factory=list)
    fetched_at: float = field(default_factory=time.time)

    @property
    def url(self) -> str:
        """带分P参数的可点击链接。"""

        base = f"https://www.bilibili.com/video/{self.bvid}" if self.bvid else f"https://www.bilibili.com/video/av{self.aid}"
        return base + (f"?p={self.page}" if self.page > 1 else "")

    @property
    def part_title(self) -> str:
        """当前分P的小标题（单P视频返回空串）。"""

        for number, title, _duration in self.pages:
            if number == self.page:
                return title
        return ""

    def to_dict(self) -> Dict[str, Any]:
        """给日志 / API 用的轻量字典。"""

        return {
            "bvid": self.bvid,
            "aid": self.aid,
            "title": self.title,
            "owner": self.owner,
            "partition": self.partition,
            "duration": self.duration,
            "page": self.page,
            "pages": len(self.pages),
            "comments": len(self.comments),
            "danmaku": len(self.danmaku),
            "has_subtitle": bool(self.subtitle),
            "has_subtitle_timeline": bool(self.subtitle_segments),
            "has_danmaku_timeline": bool(self.danmaku_timeline),
            "url": self.url,
        }


def _tags_of(data: Dict[str, Any]) -> List[str]:
    raw = data.get("tag")
    if isinstance(raw, list):
        items = [str(part).strip() for part in raw]
    else:
        items = [part.strip() for part in str(raw or "").split(",")]
    return [item for item in items if item][:10]


def build_brief(bundle: Dict[str, Any], *, target: Optional[VideoTarget] = None) -> VideoBrief:
    """把 fetch_video_bundle 的返回值整理成 VideoBrief。"""

    data = bundle.get("view") if isinstance(bundle.get("view"), dict) else {}
    video = Video.from_api(data) if data else None
    page = int(bundle.get("page") or (target.page if target else 1) or 1)
    pages_raw = bundle.get("pages") if isinstance(bundle.get("pages"), list) else []
    pages: List[Tuple[int, str, int]] = []
    for index, entry in enumerate(pages_raw, start=1):
        if not isinstance(entry, dict):
            continue
        try:
            duration = int(entry.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        pages.append((index, str(entry.get("part") or f"P{index}").strip(), duration))
    comments: List[Tuple[str, str, int]] = []
    for item in bundle.get("comments") or []:
        text = str(getattr(item, "message", "") or "").strip()
        if not text:
            continue
        comments.append((text, str(getattr(item, "user", "") or "").strip(), int(getattr(item, "like", 0) or 0)))
    danmaku: List[str] = []
    danmaku_timeline: List[Tuple[int, str]] = []
    for item in bundle.get("danmaku") or []:
        text = str(getattr(item, "text", "") or "").strip()
        if not text:
            continue
        danmaku.append(text)
        try:
            progress = int(getattr(item, "progress_ms", 0) or 0)
        except (TypeError, ValueError):
            progress = 0
        danmaku_timeline.append((max(0, progress) // 1000, text))
    subtitle_segments: List[Tuple[int, str]] = []
    for entry in bundle.get("subtitle_segments") or []:
        try:
            start, segment_text = entry
        except (TypeError, ValueError):
            continue
        clean = str(segment_text or "").strip()
        if not clean:
            continue
        try:
            seconds = int(start or 0)
        except (TypeError, ValueError):
            seconds = 0
        subtitle_segments.append((max(0, seconds), clean))
    stats = {
        "play": int(getattr(video, "play", 0) or 0),
        "danmaku": int(getattr(video, "danmaku", 0) or 0),
        "reply": int(getattr(video, "reply", 0) or 0),
        "like": int(getattr(video, "like", 0) or 0),
        "coin": int(getattr(video, "coin", 0) or 0),
        "favorite": int(getattr(video, "favorite", 0) or 0),
    }
    return VideoBrief(
        bvid=getattr(video, "bvid", "") or "",
        aid=int(getattr(video, "aid", 0) or 0),
        title=getattr(video, "title", "") or "",
        owner=getattr(video, "owner", "") or "",
        partition=getattr(video, "partition", "") or "",
        duration=int(getattr(video, "duration", 0) or 0),
        pubdate=int(getattr(video, "pubdate", 0) or 0),
        description=getattr(video, "description", "") or "",
        tags=_tags_of(data),
        pages=pages,
        page=page,
        stats=stats,
        danmaku=danmaku,
        danmaku_timeline=danmaku_timeline,
        comments=comments,
        subtitle=str(bundle.get("subtitle") or "").strip(),
        subtitle_segments=subtitle_segments,
    )


def _pubdate_text(timestamp: int) -> str:
    if not timestamp:
        return ""
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _mmss(seconds: int) -> str:
    """把秒数格式化成 mm:ss（超过一小时用 h:mm:ss）。"""

    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        total = 0
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _transcript_lines(segments: List[Tuple[int, str]], *, block_seconds: int = 90) -> List[str]:
    """把字幕片段按 block_seconds 分块，块首标 [mm:ss]，块内连成一段。"""

    lines: List[str] = []
    current = -1
    buffer: List[str] = []
    for start, text in segments:
        try:
            number = max(0, int(start)) // block_seconds
        except (TypeError, ValueError):
            number = 0
        if number != current:
            if buffer:
                lines.append(f"[{_mmss(current * block_seconds)}] " + " ".join(buffer))
            current = number
            buffer = []
        if text:
            buffer.append(text)
    if buffer:
        lines.append(f"[{_mmss(current * block_seconds)}] " + " ".join(buffer))
    return lines


def _danmaku_timeline_lines(
    items: List[Tuple[int, str]], *, block_seconds: int = 60, per_block: int = 3
) -> List[str]:
    """弹幕按分钟聚合：每块给条数 + 几条代表弹幕，能看出哪里最热闹。"""

    buckets: Dict[int, List[Tuple[int, str]]] = {}
    for start, text in items:
        if not text:
            continue
        try:
            number = max(0, int(start)) // block_seconds
        except (TypeError, ValueError):
            number = 0
        buckets.setdefault(number, []).append((start, text))
    lines: List[str] = []
    for number in sorted(buckets):
        entries = sorted(buckets[number], key=lambda item: item[0])
        samples = [text[:MAX_DANMAKU_LINE] for _start, text in entries[:per_block]]
        head = f"[{_mmss(number * block_seconds)}] {len(entries)} 条："
        lines.append(head + " / ".join(samples))
    return lines


def render_brief(brief: VideoBrief, *, max_chars: int = 3500, detail: str = "brief") -> str:
    """把 VideoBrief 渲染成模型好读的纯文本（受 max_chars 限制）。

    detail="full" 走「深度读」：完整字幕时间轴、按分钟聚合的弹幕时间线、
    更多热评、更长的简介与完整分P列表；默认 brief 保持省 token 的老格式。
    """

    full = str(detail or "").strip().lower() in ("full", "deep", "detail")
    limit = max(500, int(max_chars or 3500))
    lines: List[str] = []
    lines.append(f"【B站视频】{brief.title or brief.bvid or brief.url}")
    if full:
        lines.append("深度读：完整字幕时间轴 + 弹幕时间线 + 更多热评")
    meta: List[str] = []
    if brief.owner:
        meta.append(f"UP主 {brief.owner}")
    if brief.partition:
        meta.append(f"分区 {brief.partition}")
    if brief.duration:
        meta.append(f"时长 {duration_text(brief.duration)}")
    if meta:
        lines.append(" · ".join(meta))
    stats = brief.stats or {}
    stat_parts = [
        f"播放 {count_text(stats.get('play', 0))}",
        f"弹幕 {count_text(stats.get('danmaku', 0))}",
        f"评论 {count_text(stats.get('reply', 0))}",
        f"点赞 {count_text(stats.get('like', 0))}",
        f"投币 {count_text(stats.get('coin', 0))}",
        f"收藏 {count_text(stats.get('favorite', 0))}",
    ]
    lines.append("数据：" + " ｜ ".join(stat_parts))
    if brief.pubdate:
        lines.append(f"发布：{_pubdate_text(brief.pubdate)}")
    if len(brief.pages) > 1:
        part = brief.part_title or ""
        suffix = f"（当前 P{brief.page}：{part}）" if part else f"（当前 P{brief.page}）"
        lines.append(f"分P：共 {len(brief.pages)} P{suffix}")
        pages_cap = len(brief.pages) if full else 8
        listing = "、".join(
            f"P{number} {title}" for number, title, _d in brief.pages[:pages_cap]
        )
        if listing:
            lines.append(
                f"分P列表：{listing}" + ("……" if len(brief.pages) > pages_cap else "")
            )
    if brief.tags:
        lines.append("标签：" + " / ".join(brief.tags))
    if brief.description:
        desc_cap = max(MAX_DESC_CHARS, 800) if full else MAX_DESC_CHARS
        desc = brief.description.strip()
        if len(desc) > desc_cap:
            desc = desc[:desc_cap].rstrip() + "……"
        lines.append("简介：" + desc)
    if full:
        timeline = _transcript_lines(brief.subtitle_segments) if brief.subtitle_segments else []
        if timeline:
            lines.append("字幕时间轴（[分:秒] 该时间点在说什么）：")
            lines.extend(timeline)
        elif brief.subtitle:
            lines.append("字幕（全文）：" + brief.subtitle)
    elif brief.subtitle:
        lines.append("字幕（节选）：" + brief.subtitle)
    if brief.comments:
        lines.append("热评：")
        comment_cap = 20 if full else 10
        text_cap = 300 if full else 160
        for index, (text, user, like) in enumerate(brief.comments[:comment_cap], start=1):
            who = f"{user}：" if user else ""
            lines.append(f"{index}. （{count_text(like)} 赞）{who}{text[:text_cap]}")
    if full and brief.danmaku_timeline:
        lines.append("弹幕时间线（按分钟，条数多的是高能段落）：")
        lines.extend(_danmaku_timeline_lines(brief.danmaku_timeline))
    elif brief.danmaku:
        sampled = [item[:MAX_DANMAKU_LINE] for item in brief.danmaku[:24]]
        lines.append("弹幕采样：" + " / ".join(sampled))
    lines.append("链接：" + brief.url)
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: max(1, limit - 12)].rstrip() + "\n……（已截断）"
    return text


class VideoCache:
    """按缓存键保存 VideoBrief，带 TTL 与条数上限。"""

    def __init__(self, ttl_seconds: float = 900.0, max_items: int = 64) -> None:
        self.ttl_seconds = float(ttl_seconds or 0)
        self.max_items = max(1, int(max_items))
        self._items: Dict[str, Tuple[float, VideoBrief]] = {}

    def get(self, key: str, *, now: Optional[float] = None) -> Optional[VideoBrief]:
        entry = self._items.get(key)
        if not entry:
            return None
        expires_at, brief = entry
        if now is None:
            now = time.time()
        if expires_at <= now:
            self._items.pop(key, None)
            return None
        return brief

    def put(self, key: str, brief: VideoBrief, *, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()
        if len(self._items) >= self.max_items:
            oldest = sorted(self._items.items(), key=lambda item: item[1][0])[: max(1, self.max_items // 4)]
            for name, _value in oldest:
                self._items.pop(name, None)
        self._items[key] = (now + max(0.0, self.ttl_seconds), brief)

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)


def cache_key(
    target: VideoTarget,
    *,
    comment_limit: int,
    danmaku_limit: int,
    use_subtitle: bool,
    subtitle_chars: int = 0,
) -> str:
    """缓存键带上读取参数，换个参数就重新抓（深度读与摘要不能共用缓存）。"""

    key = f"{target.key}|c{int(comment_limit)}|d{int(danmaku_limit)}|s{1 if use_subtitle else 0}"
    if subtitle_chars:
        key += f".{int(subtitle_chars)}"
    return key


async def read_brief(
    client: BilibiliClient,
    target: VideoTarget,
    *,
    comment_limit: int = 8,
    danmaku_limit: int = 30,
    use_subtitle: bool = True,
    subtitle_chars: int = 1200,
    cache: Optional[VideoCache] = None,
) -> VideoBrief:
    """读取一条视频的完整资料；短链会先解析成真实地址。"""

    if target is None or target.empty:
        raise BiliReadError("没有找到 B 站视频链接或 BV 号")
    resolved = target
    if resolved.short_url:
        try:
            final_url = await client.resolve_redirect(resolved.short_url)
        except BilibiliError as exc:
            raise BiliReadError(f"短链解析失败：{exc}") from exc
        parsed = parse_target(final_url)
        if parsed is None or parsed.empty:
            raise BiliReadError("短链没有指向 B 站视频")
        parsed.original = resolved.original
        resolved = parsed
    key = cache_key(
        resolved,
        comment_limit=comment_limit,
        danmaku_limit=danmaku_limit,
        use_subtitle=use_subtitle,
        subtitle_chars=subtitle_chars if use_subtitle else 0,
    )
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            return cached
    try:
        bundle = await client.fetch_video_bundle(
            bvid=resolved.bvid,
            aid=resolved.aid,
            page=resolved.page,
            comment_limit=max(0, int(comment_limit)),
            danmaku_limit=max(0, int(danmaku_limit)),
            subtitle_chars=max(0, int(subtitle_chars)) if use_subtitle else 0,
        )
    except BilibiliError as exc:
        raise BiliReadError(f"读取视频失败：{exc}") from exc
    brief = build_brief(bundle, target=resolved)
    if cache is not None:
        cache.put(key, brief)
    return brief
