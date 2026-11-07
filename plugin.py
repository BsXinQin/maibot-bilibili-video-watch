"""沁の刷B站视频插件。

按白名单聊天流的用户兴趣，定时刷 B 站并挑视频分享，也能读群友贴的视频：

- 抽帧看画面（视觉模型）+ 弹幕 / 热门评论 / CC 字幕一起参与判断；
- LLM 语义判断，按人设口吻生成简短介绍；
- 排除主流官方物料，按「新鲜资讯 / 社区创作 / 玩家投稿 / 外网搬运」分类抓取；
- 自动分享与主动搜索各自独立冷却；
- 已刷名单：名单内视频不再自动分享；
- ``bili_read`` 工具：别人贴链接自动 brief 快速读，@ 机器人或说「详细看看」时 full 深度读并抽帧；
- ``bili_search`` / ``bili_shiwei`` 工具：主动搜索分享、赤石整活视频。

聊天指令已全部移除（平台会拦截 `/` 开头的消息），只通过工具由模型调用。
宿主能力都通过 ``self.ctx`` 调用，模型由 ``model_config.toml`` 的任务配置决定。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from maibot_sdk import HookHandler, MaiBotPlugin, Tool

try:  # 文档推荐的导入路径（HookMode / ToolParameterInfo 都定义在 types 里）
    from maibot_sdk.types import ErrorPolicy, HookMode, ToolParameterInfo, ToolParamType
except ImportError:  # 兼容把 HookMode 只在 components 里导出的旧版本 SDK
    from maibot_sdk.components import HookMode
    from maibot_sdk.types import ErrorPolicy, ToolParameterInfo, ToolParamType

try:  # 宿主正常按包加载插件
    from .bilibili import BilibiliClient, BilibiliError, NAV_URL, Video, merge_videos
    from .bili_read import (
        BiliReadError,
        VideoBrief,
        VideoCache,
        build_brief,
        extract_targets,
        parse_target,
        read_brief,
        render_brief,
    )
    from .config import BilibiliInterestShareConfig
    from .config_edit import (
        describe_write_back_state,
        find_config_path,
        should_write_back,
        stream_identity,
        update_persona_seed_interests,
        update_stream_auto_interests,
    )
    from .interest import (
        InterestProfile,
        ScoreResult,
        extract_interest_keywords,
        normalize_term,
        rank_videos,
    )
    from .llm_judge import LlmJudge
    from .media import FrameExtractor, cleanup_dir, safe_bvid
    from .persona import PersonaProvider
    from .store import COOLDOWN_ACTIVE_SEARCH, COOLDOWN_SHIWEI, SeenStore
    from .textfilter import filter_comments, filter_danmaku
except ImportError:  # 兜底：插件目录被当作顶层模块加载时退回同级导入
    import os as _os
    import sys as _sys

    _HERE = _os.path.dirname(_os.path.abspath(__file__))
    if _HERE not in _sys.path:
        _sys.path.insert(0, _HERE)
    from bilibili import BilibiliClient, BilibiliError, NAV_URL, Video, merge_videos
    from bili_read import (
        BiliReadError,
        VideoBrief,
        VideoCache,
        build_brief,
        extract_targets,
        parse_target,
        read_brief,
        render_brief,
    )
    from config import BilibiliInterestShareConfig
    from config_edit import (
        describe_write_back_state,
        find_config_path,
        should_write_back,
        stream_identity,
        update_persona_seed_interests,
        update_stream_auto_interests,
    )
    from interest import (
        InterestProfile,
        ScoreResult,
        extract_interest_keywords,
        normalize_term,
        rank_videos,
    )
    from llm_judge import LlmJudge
    from media import FrameExtractor, cleanup_dir, safe_bvid
    from persona import PersonaProvider
    from store import COOLDOWN_ACTIVE_SEARCH, COOLDOWN_SHIWEI, SeenStore
    from textfilter import filter_comments, filter_danmaku

logger = logging.getLogger("plugin.bilibili_interest_share")

PLUGIN_ID = "github.BsXinQin.bilibili_video_watch"
_MAX_HISTORY_ITEMS = 8

# 深度读（bili_read 的 detail=full）：字幕全文时间轴 + 弹幕时间线，默认值刻意
# 比摘要档大，但仍然有上限，避免一次读取把上下文撑爆。
DEEP_READ_MAX_CHARS = 9000
DEEP_READ_COMMENT_LIMIT = 15
DEEP_READ_DANMAKU_LIMIT = 120
DEEP_READ_SUBTITLE_CHARS = 12000


def normalize_detail(value: Any) -> str:
    """把 detail 参数（full / deep / detail / 带空格等）归一化成 full 或 brief。"""

    text = str(value or "").strip().lower()
    return "full" if text in ("full", "deep", "detail", "深度", "详细") else "brief"
# 白名单 stream_id 缓存时间（秒）
_ALLOWED_STREAMS_TTL_SECONDS = 300.0
# 自己写完 config.toml 后的保护期（秒）：这段时间内触发的重载不再跑启动轮
_CONFIG_WRITE_GUARD_SECONDS = 180.0
_CONFIG_WRITE_META = "last_config_write_at"


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------


def _parse_hhmm(value: str) -> Optional[int]:
    """``"8:30"`` → 510（当天第几分钟）。"""

    text = (value or "").strip()
    if not text:
        return None
    parts = text.replace("：", ":").split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        return None
    return min(hour * 60 + minute, 24 * 60)


def _parse_active_hours(value: str) -> Optional[Tuple[int, int]]:
    """解析 ``"9:00-23:30"``；空字符串或非法值返回 ``None``（表示全天）。"""

    text = (value or "").strip()
    if not text:
        return None
    for separator in ("-", "~", "～", "—"):
        if separator in text:
            start_text, _, end_text = text.partition(separator)
            start = _parse_hhmm(start_text)
            end = _parse_hhmm(end_text)
            if start is None or end is None:
                return None
            return start, end
    return None


def _in_active_window(value: str, *, now: Optional[datetime] = None) -> bool:
    """判断当前时间是否落在活跃时间段内（支持跨午夜）。"""

    window = _parse_active_hours(value)
    if window is None:
        return True
    start, end = window
    current = now or datetime.now()
    minute = current.hour * 60 + current.minute
    if start == end:
        return True
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


def _structured_payload_text(value: Any, depth: int = 0) -> str:
    """从结构化消息段（B 站小程序卡片等）里挖出字符串。"""

    if value is None or depth > 5:
        return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text[:1] in "{[":
            try:
                return _structured_payload_text(json.loads(text), depth + 1)
            except (TypeError, ValueError):
                pass
        return text
    if isinstance(value, dict):
        return " ".join(_structured_payload_text(item, depth + 1) for item in value.values())
    if isinstance(value, list):
        return " ".join(_structured_payload_text(item, depth + 1) for item in value)
    if isinstance(value, bool):
        return ""
    return str(value)


def _message_text(message: Any) -> str:
    """从宿主序列化的消息 dict 里取纯文本。

    宿主把文本段写成 {"type": "text", "data": "正文"}（data 是字符串），
    这里两种形态都要认；plain_text 是老字段，顺带兼容。
    """

    if not isinstance(message, dict):
        return ""
    for key in ("processed_plain_text", "plain_text"):
        text = message.get(key)
        if isinstance(text, str) and text.strip():
            return text.strip()
    raw = message.get("raw_message")
    if isinstance(raw, str):
        return raw.strip()
    parts: List[str] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            data = item.get("data")
            if isinstance(data, str):
                if data.strip():
                    parts.append(data)
            elif isinstance(data, dict):
                text = data.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item.get("text"), str):
                parts.append(item["text"])
    return "".join(parts).strip()


def _message_link_text(message: Any) -> str:
    """取消息里可能带 B 站链接的文本：纯文本不够时再挖小程序卡片。"""

    text = _message_text(message)
    if not isinstance(message, dict) or _BILI_URL_RE.search(text):
        return text
    extras: List[str] = []
    raw = message.get("raw_message")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").lower() in {"text", "plain_text"}:
                continue
            extra = _structured_payload_text(item.get("data"))
            if extra:
                extras.append(extra)
    info = message.get("message_info")
    additional = info.get("additional_config") if isinstance(info, dict) else None
    if isinstance(additional, dict):
        extra = _structured_payload_text(additional.get("platform_card_payloads"))
        if extra:
            extras.append(extra)
    joined = " ".join(part for part in extras if part)
    if joined and _BILI_URL_RE.search(joined):
        logger.debug("从结构化消息段里找到 B 站链接")
        return f"{text} {joined}".strip()
    return text


def _message_group_id(message: Any) -> str:
    """从消息字典里取群号（私聊返回空串）。"""

    if not isinstance(message, dict):
        return ""
    info = message.get("message_info")
    if isinstance(info, dict):
        group = info.get("group_info")
        if isinstance(group, dict):
            value = str(group.get("group_id") or "").strip()
            if value:
                return value
    value = str(message.get("group_id") or "").strip()
    return value


def _message_mentions_me(message: Dict[str, Any]) -> bool:
    """判断这条消息是不是在 @ 机器人（宿主字段或 OneBot at 段）。"""

    if not isinstance(message, dict):
        return False
    for key in ("at_me", "is_at_me", "is_at", "mentioned"):
        if message.get(key) is True:
            return True
    raw = message.get("raw_message")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            segment_type = str(item.get("type") or "").lower()
            if segment_type in {"at", "mention"}:
                return True
    return False


def wants_full_read(
    message: Dict[str, Any], text: str, keywords: Sequence[str]
) -> bool:
    """判断这条消息要不要深度读（@ 机器人，或说了「详细看看」这类话）。"""

    if _message_mentions_me(message):
        return True
    body = (text or "").lower()
    if not body:
        return False
    return any(str(word).strip() and str(word).strip().lower() in body for word in keywords)


def _stream_id_of(stream: Any) -> str:
    """从 stream 对象 / dict / 字符串里取 stream_id。"""

    if isinstance(stream, str):
        return stream.strip()
    if isinstance(stream, dict):
        for key in ("stream_id", "session_id"):
            value = stream.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


_BILI_URL_RE = re.compile(r"(?:bilibili\.com|b23\.tv|BV[0-9A-Za-z]{8,})", re.I)


def _normalize_terms(values: Sequence[Any]) -> List[str]:
    """把兴趣词归一成「去重 + 排序」的列表，用于稳定比较和写盘。"""

    cleaned = {str(item).strip() for item in values if str(item or "").strip()}
    return sorted(cleaned)


def _message_user_id(message: Any) -> str:
    """从消息字典里取发送者 user_id。"""

    if not isinstance(message, dict):
        return ""
    info = message.get("message_info")
    if isinstance(info, dict):
        user = info.get("user_info")
        if isinstance(user, dict):
            value = str(user.get("user_id") or "").strip()
            if value:
                return value
    for key in ("user_id", "sender_id"):
        value = str(message.get(key) or "").strip()
        if value:
            return value
    return ""


def _looks_like_own_share(message: Any) -> bool:
    """判断一条消息是不是（自己或别人）转发的 B 站视频链接。

    这类消息里全是标题、`https://www.bilibili.com/video/BV...`、「刚刷到」，
    对「这个群喜欢什么」没有参考价值，提取兴趣时要排掉。
    """

    if not isinstance(message, dict):
        return False
    text = message.get("processed_plain_text") or ""
    if not isinstance(text, str) or not text.strip():
        text = ""
        raw = message.get("raw_message")
        if isinstance(raw, str):
            text = raw
    if not text:
        return False
    return bool(_BILI_URL_RE.search(text))


def filter_messages_by_lookback(
    messages: Sequence[Dict[str, Any]], lookback_hours: float
) -> List[Dict[str, Any]]:
    """按时间窗过滤消息（宿主 ``get_recent`` 不支持时间参数）。

    ``timestamp`` 缺失或无法解析时保留该条，避免把格式差异当成「没有消息」。
    """

    if lookback_hours <= 0:
        return list(messages)
    cutoff = time.time() - lookback_hours * 3600
    kept: List[Dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        raw = message.get("timestamp")
        try:
            stamp = float(str(raw).strip()) if raw not in (None, "") else None
        except (TypeError, ValueError):
            stamp = None
        if stamp is None or stamp >= cutoff:
            kept.append(message)
    return kept


def format_share_header(video: Video) -> str:
    """B 站标准转发格式：【标题】 链接。"""

    return f"【{video.title}】 {video.url}"


# ----------------------------------------------------------------------
# 插件主体
# ----------------------------------------------------------------------


class BilibiliInterestSharePlugin(MaiBotPlugin):
    """定时刷 B 站并按兴趣分享视频。"""

    config_model = BilibiliInterestShareConfig

    def __init__(self) -> None:
        super().__init__()
        self._loop_task: Optional[asyncio.Task] = None
        self._client: Optional[BilibiliClient] = None
        self._store: Optional[SeenStore] = None
        self._frames: Optional[FrameExtractor] = None
        self._judge: Optional[LlmJudge] = None
        self._persona: Optional[PersonaProvider] = None
        self._data_dir: Optional[Path] = None
        self._allowed_stream_ids: Set[str] = set()
        self._allowed_streams_at: float = 0.0
        # 宿主里登记过的 bot 账号（用来过滤自己 / 同类 bot 发的消息）
        self._bot_account_ids: Set[str] = set()
        self._stream_fallbacks: Dict[str, List[str]] = {}
        self._stream_overrides: Dict[str, str] = {}
        # 本轮攒下来的配置写回操作：写 config.toml 会让宿主重载插件、打断当前这一轮，
        # 所以先记下来，等这一轮跑完再一次性写盘。
        self._pending_stream_writes: Dict[str, Tuple[Any, List[str]]] = {}
        self._pending_seed_terms: List[str] = []
        self._round_lock = asyncio.Lock()
        self.last_round: Optional[Dict[str, Any]] = None
        self.last_round_at: Optional[float] = None
        self.next_run_at: Optional[float] = None
        # 启动时检查到的 B 站昵称（没有 Cookie 或校验失败时为空）
        self._login_uname: str = ""
        # 读视频（bili_read）：按视频缓存的解析结果 + 自动读的防重复表
        self._read_cache: Optional[VideoCache] = None
        self._auto_read_seen: Dict[str, float] = {}
        # 画面描述缓存（bvid -> (时间戳, 描述)），自动读和工具读共用，避免重复抽帧
        self._vision_cache: Dict[str, Tuple[float, str]] = {}
        # 白名单里按 user_id 配置的私聊会话：贴链接默认按深度读
        self._private_stream_ids: Set[str] = set()

    # -- 生命周期 ------------------------------------------------------

    async def on_load(self) -> None:
        config = self.config
        if not config.plugin.enabled:
            logger.info("沁の刷B站视频插件已禁用，跳过启动")
            return

        self._data_dir = self._resolve_data_dir()
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._store = SeenStore(
            self._data_dir / "state.json",
            dedupe_days=config.advanced.dedupe_days,
            max_records=config.advanced.max_seen_records,
        )
        self._store.load()

        self._client = BilibiliClient(
            timeout=config.advanced.request_timeout_seconds,
            cookie=self._effective_cookie(),
            user_agent=config.advanced.user_agent,
        )
        self._read_cache = VideoCache(ttl_seconds=int(config.read.cache_seconds))
        self._auto_read_seen.clear()
        self._vision_cache.clear()
        self._frames = FrameExtractor(
            ffmpeg_path=config.advanced.ffmpeg_path,
            frame_count=config.vision.frame_count,
            max_width=config.vision.max_width,
            max_download_mb=config.vision.max_download_mb,
            timeout=config.vision.timeout_seconds,
        )
        self._persona = PersonaProvider(
            enabled=config.persona.enabled,
            config_get=self._config_getter(),
            override_name=config.persona.name,
            override_personality=config.persona.personality,
            override_reply_style=config.persona.reply_style,
            style_hint=config.persona.style_hint,
            cache_seconds=config.persona.cache_seconds,
        )
        self._judge = LlmJudge(
            getattr(self.ctx, "llm", None),
            task_name=config.interest.llm_task_name,
            vision_task_name=config.vision.task_name,
            temperature=config.interest.llm_temperature,
            persona_provider=self._persona,
        )
        self._stream_fallbacks.clear()
        self._stream_overrides.clear()
        self._bot_account_ids.clear()
        await self._persona.refresh()
        await self._refresh_allowed_streams()
        await self._refresh_bot_account_ids()

        self._loop_task = asyncio.create_task(
            self._run_loop(), name="bilibili-interest-share-loop"
        )
        logger.info(
            "沁の刷B站视频插件已启动：每 %s 分钟一轮，白名单 %s 个会话",
            config.schedule.interval_minutes,
            len([item for item in config.streams if item.enabled]),
        )
        if config.account.check_on_start:
            asyncio.create_task(
                self._check_login_on_start(), name="bilibili-interest-share-login-check"
            )

    async def _check_login_on_start(self) -> None:
        """启动时用 nav 接口检查一次 Cookie，失效就在日志里提示手动重填。"""

        if self._client is None:
            return
        cookie = self._effective_cookie()
        if not cookie:
            logger.info("沁の刷B站视频：未配置 B 站 Cookie，以匿名方式访问（读不到字幕、排行榜易被风控）")
            return
        try:
            response = await self._client.http.get(NAV_URL)
            payload = response.json() if response.status_code == 200 else {}
        except Exception as exc:  # noqa: BLE001 - 启动检查失败不影响运行
            logger.debug("检查 B 站登录状态失败：%s", exc)
            return
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict) and data.get("isLogin"):
            self._login_uname = str(data.get("uname") or "").strip()
            logger.info(
                "沁の刷B站视频：B 站登录状态正常（%s）", self._login_uname or "已登录"
            )
        else:
            self._login_uname = ""
            logger.warning(
                "沁の刷B站视频：B 站 Cookie 可能已失效，请在插件配置的「账号登录 → B 站 Cookie」里重新粘贴"
            )

    async def on_unload(self) -> None:
        task = self._loop_task
        self._loop_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001
                logger.warning("后台任务退出时出错：%s", exc)
        if self._store is not None:
            try:
                self._store.save()
            except Exception as exc:  # noqa: BLE001
                logger.warning("保存状态失败：%s", exc)
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:  # noqa: BLE001
                logger.warning("关闭 HTTP 客户端失败：%s", exc)
        self._client = None
        self._judge = None
        self._persona = None
        self._read_cache = None
        self._auto_read_seen.clear()

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        logger.info("沁の刷B站视频配置已更新（scope=%s, version=%s），重启后台循环", scope, version)
        await self.on_unload()
        await self.on_load()

    def get_components(self) -> List[Dict[str, Any]]:
        """把工具 / 命令上的 ``chat_scope`` 提到组件顶层。

        SDK 的 ``collect_components`` 只对 ReplyExtension 做这个提升，
        ``Tool`` 的 ``chat_scope`` 会留在 metadata 里，而 Runner 读的是顶层字段。
        """

        components = super().get_components()
        for component in components:
            if not isinstance(component, dict):
                continue
            if component.get("chat_scope"):
                continue
            metadata = component.get("metadata")
            if isinstance(metadata, dict) and metadata.get("chat_scope"):
                component["chat_scope"] = metadata["chat_scope"]
        return components

    # -- 后台循环 ------------------------------------------------------

    async def _run_loop(self) -> None:
        config = self.config
        interval = max(60.0, float(config.schedule.interval_minutes) * 60.0)
        if config.schedule.run_on_start and not self._just_wrote_config():
            await self._safe_round(reason="startup")
        elif config.schedule.run_on_start:
            logger.info("沁の刷B站视频：刚写过配置文件触发的重载，跳过启动轮，等下一个周期")
        while True:
            jitter = 0.0
            if config.schedule.jitter_seconds > 0:
                jitter = random.uniform(0.0, float(config.schedule.jitter_seconds))
            delay = max(30.0, interval + jitter)
            self.next_run_at = time.time() + delay
            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return
            await self._safe_round(reason="scheduled")

    def _just_wrote_config(self) -> bool:
        """刚由插件自己写过 config.toml 吗（写入会触发宿主重载，避免重载后再跑一轮）。"""

        if self._store is None:
            return False
        try:
            stamp = float(self._store.get_meta(_CONFIG_WRITE_META, 0.0) or 0.0)
        except (TypeError, ValueError):
            return False
        if stamp <= 0.0:
            return False
        return time.time() - stamp < _CONFIG_WRITE_GUARD_SECONDS

    async def _safe_round(self, *, reason: str) -> None:
        try:
            summary = await self.run_round(reason=reason)
            if self._debug_enabled():
                logger.info("分享轮次结束：%s", summary)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台循环不能因单轮失败而退出
            logger.warning("分享轮次失败：%s", exc, exc_info=self._debug_enabled())

    # -- 主流程 --------------------------------------------------------

    async def run_round(self, *, reason: str = "scheduled", manual: bool = False) -> Dict[str, Any]:
        """跑一轮自动分享。"""

        config = self.config
        if not config.plugin.enabled:
            return {"ok": False, "skipped": "disabled", "reason": reason, "fetched": 0, "shared": 0}
        if not manual and not _in_active_window(config.schedule.active_hours):
            return {
                "ok": True,
                "skipped": "outside_active_hours",
                "reason": reason,
                "fetched": 0,
                "shared": 0,
            }
        targets = [item for item in config.streams if item.enabled]
        if not targets:
            return {"ok": True, "skipped": "no_targets", "reason": reason, "fetched": 0, "shared": 0}
        if self._client is None or self._store is None:
            return {"ok": False, "skipped": "not_loaded", "reason": reason, "fetched": 0, "shared": 0}

        async with self._round_lock:
            # 每轮开始前刷新人设与白名单缓存（都带 TTL，不会频繁请求宿主）
            if self._persona is not None:
                try:
                    await self._persona.ensure()
                except Exception as exc:  # noqa: BLE001 - 人设读不到就用中性语气
                    logger.debug("刷新人设失败：%s", exc)
            await self._ensure_allowed_streams()
            if not self._bot_account_ids:
                await self._refresh_bot_account_ids()
            # 「人设初始兴趣」是通用配置：没有就按人设生成一次（轮末写回配置）
            if not self._configured_persona_seed():
                try:
                    await self._ensure_persona_seed_interests()
                except Exception as exc:  # noqa: BLE001 - 生成失败不影响正常分享
                    logger.debug("生成人设初始兴趣失败：%s", exc)
            resolved: List[Tuple[Any, str, InterestProfile]] = []
            for target in targets:
                stream_id = await self._resolve_stream(target)
                if not stream_id:
                    logger.warning("白名单会话无法解析：%s", target.name or target.group_id)
                    continue
                profile = await self._build_profile(target, stream_id)
                if not profile:
                    logger.info("会话 %s 没有可用兴趣，跳过", stream_id)
                    continue
                resolved.append((target, stream_id, profile))
            if not resolved:
                self._flush_pending_config_writes()
                return {
                    "ok": True,
                    "skipped": "no_stream",
                    "reason": reason,
                    "fetched": 0,
                    "shared": 0,
                }

            keywords = self._collect_search_keywords([item[2] for item in resolved])
            videos, category_of = await self._fetch_candidates(keywords)
            videos = self._prefilter(videos)
            shared: List[str] = []
            target_details: List[Dict[str, Any]] = []
            for target, stream_id, profile in resolved:
                try:
                    detail = await self._process_target(
                        target, stream_id, profile, videos, category_of
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - 单个会话失败不影响其它会话
                    logger.warning("处理会话 %s 失败：%s", stream_id, exc)
                    detail = {
                        "target": target.name or stream_id,
                        "stream_id": stream_id,
                        "shared": 0,
                        "matched": [],
                        "bvids": [],
                    }
                target_details.append(detail)
                shared.extend(detail.get("bvids") or [])

            summary = {
                "ok": True,
                "reason": reason,
                "fetched": len(videos),
                "shared": len(shared),
                "keywords": keywords,
                "targets": target_details,
                "target_count": len(resolved),
                "at": time.time(),
                "bvids": shared,
            }
            self.last_round = summary
            self.last_round_at = time.time()
            self._store.record_run(
                {key: value for key, value in summary.items() if key != "bvids"}
            )
            self._store.save()
            # 这一轮彻底跑完，再把攒下的配置写回落盘（避免写文件触发重载打断本轮）
            self._flush_pending_config_writes()
            return summary

    async def _process_target(
        self,
        target: Any,
        stream_id: str,
        profile: InterestProfile,
        videos: Sequence[Video],
        category_of: Dict[str, str],
    ) -> Dict[str, Any]:
        """给一个会话挑视频并分享，返回本次会话的分享详情。"""

        config = self.config
        detail: Dict[str, Any] = {
            "target": target.name or stream_id,
            "stream_id": stream_id,
            "shared": 0,
            "matched": [],
            "bvids": [],
        }
        allowed = [item.strip() for item in (target.categories or []) if item and item.strip()]
        candidates = [
            video
            for video in videos
            if not allowed
            or not category_of.get(video.bvid)
            or category_of.get(video.bvid) in allowed
        ]
        # 每个类别可以有自己的最低播放量（0 = 沿用 [filter].min_play）
        category_min_play = {
            category.name: int(category.min_play)
            for category in config.categories
            if category.min_play and category.min_play > 0
        }
        if category_min_play:
            candidates = [
                video
                for video in candidates
                if video.play
                >= category_min_play.get(category_of.get(video.bvid, ""), 0)
            ]
        # 已刷名单内的视频不再自动分享
        candidates = [video for video in candidates if not self._store.is_seen(video.bvid)]
        if not candidates:
            return detail

        results = rank_videos(
            candidates,
            profile,
            now=time.time(),
            min_score=config.scoring.min_score,
            require_interest_match=config.scoring.require_interest_match,
            interest_weight=config.scoring.interest_weight,
            title_match_boost=config.scoring.title_match_boost,
            hot_weight=config.scoring.hot_weight,
            freshness_weight=config.scoring.freshness_weight,
            freshness_days=config.scoring.freshness_days,
            min_duration=config.filter.min_duration_seconds,
            max_duration=config.filter.max_duration_seconds,
            min_play=config.filter.min_play,
            min_like=config.filter.min_like,
            block_keywords=config.filter.block_keywords,
            block_up_names=config.filter.block_up_names,
            exclude_partitions=config.filter.exclude_partitions,
            use_tags=config.interest.use_tags,
            tag_weight=config.interest.tag_weight,
        )
        for result in results:
            result.category = category_of.get(result.video.bvid, "")
        if not results:
            return detail

        limit = int(target.max_share_per_round or config.schedule.max_share_per_round or 1)
        picked = await self._pick_candidates(results, profile, limit)
        shared: List[str] = []
        for result, judged in picked:
            narrative_text = str((judged or {}).get("narrative") or "").strip()
            if not narrative_text:
                narrative_text = (result.video.description or "").strip()[
                    : int(config.share.casual_max_chars)
                ]
            sent = await self._send_parts(
                stream_id,
                result.video,
                narrative_text,
                kind="auto",
            )
            if sent:
                self._store.mark_shared(
                    result.video,
                    narrative=narrative_text,
                    kind="auto",
                    stream_id=stream_id,
                )
                shared.append(result.video.bvid)
        detail["shared"] = len(shared)
        detail["bvids"] = shared
        detail["matched"] = [
            {"bvid": result.video.bvid, "title": result.video.title, "score": round(result.score, 2)}
            for result in results[:6]
        ]
        return detail

    async def _pick_candidates(
        self,
        results: Sequence[ScoreResult],
        profile: InterestProfile,
        limit: int,
    ) -> List[Tuple[ScoreResult, Optional[Dict[str, Any]]]]:
        """用 LLM 语义判断筛选最终入选视频（含抽帧识图）。"""

        config = self.config
        limit = max(1, int(limit))
        # 每个类别可以有自己的每轮分享上限（0 = 不单独限制）
        category_caps = {
            str(category.name): int(category.max_share_per_round)
            for category in config.categories
            if category.name and int(category.max_share_per_round or 0) > 0
        }
        per_category: Dict[str, int] = {}

        def _over_category_cap(result: ScoreResult) -> bool:
            name = str(result.category or "")
            cap = category_caps.get(name)
            if not cap:
                return False
            return per_category.get(name, 0) >= cap

        def _count_category(result: ScoreResult) -> None:
            name = str(result.category or "")
            if name in category_caps:
                per_category[name] = per_category.get(name, 0) + 1

        if not config.interest.use_llm_judge or self._judge is None or not self._judge.available:
            simple: List[Tuple[ScoreResult, Optional[Dict[str, Any]]]] = []
            for result in results:
                if _over_category_cap(result):
                    continue
                simple.append((result, None))
                _count_category(result)
                if len(simple) >= limit:
                    break
            return simple

        interests = [term for term, _weight in profile.sorted_terms()[:10]]
        max_check = max(limit, int(config.interest.llm_max_candidates or 0), 1)
        picked: List[Tuple[ScoreResult, Optional[Dict[str, Any]]]] = []
        for result in results[:max_check]:
            video = result.video
            danmaku, comments, subtitle = await self._collect_detail(video)
            judged = await self._judge.judge(
                video,
                interests=interests,
                danmaku=danmaku,
                comments=comments,
                subtitle=subtitle,
                casual_max_chars=config.share.casual_max_chars,
            )
            if judged is not None:
                if not judged.get("relevant"):
                    continue
                if float(judged.get("score") or 0.0) < 4.0:
                    continue
                category = str(judged.get("category") or "").strip()
                if category:
                    result.category = category

            # 只对入选视频抽帧识图，减少下载与视觉模型调用
            frames_desc = ""
            if config.vision.enabled and self._frames is not None:
                frames = await self._capture_frames(video)
                if frames:
                    frames_desc = await self._judge.describe_frames(video, frames)
                    if frames_desc:
                        refined = await self._judge.judge(
                            video,
                            interests=interests,
                            danmaku=danmaku,
                            comments=comments,
                            subtitle=subtitle,
                            frames_desc=frames_desc,
                            casual_max_chars=config.share.casual_max_chars,
                        )
                        if refined is not None:
                            judged = refined

            if _over_category_cap(result):
                continue
            picked.append((result, judged))
            _count_category(result)
            if len(picked) >= limit:
                break
        return picked

    async def _collect_detail(self, video: Video) -> Tuple[List[str], List[str], str]:
        """抓取弹幕 / 热门评论 / 字幕（失败时返回空）。"""

        config = self.config
        danmaku: List[str] = []
        comments: List[str] = []
        subtitle = ""
        if self._client is None:
            return danmaku, comments, subtitle

        if config.danmaku.danmaku_enabled:
            try:
                raw = await self._client.fetch_danmaku(
                    video.bvid, limit=config.danmaku.danmaku_fetch_limit
                )
                danmaku = filter_danmaku(
                    raw,
                    limit=config.danmaku.danmaku_limit,
                    filter_ai=config.danmaku.filter_ai_spam,
                )
            except BilibiliError as exc:
                logger.debug("抓取弹幕失败 %s：%s", video.bvid, exc)
        if config.danmaku.comments_enabled:
            try:
                raw_comments = await self._client.fetch_hot_comments(
                    video.bvid, limit=config.danmaku.comments_limit
                )
                comments = filter_comments(
                    raw_comments,
                    limit=config.danmaku.comments_limit,
                    filter_ai=config.danmaku.filter_ai_spam,
                )
            except BilibiliError as exc:
                logger.debug("抓取评论失败 %s：%s", video.bvid, exc)
        if config.interest.use_subtitles:
            try:
                subtitle = await self._client.fetch_subtitle(video.bvid)
            except BilibiliError as exc:
                logger.debug("抓取字幕失败 %s：%s", video.bvid, exc)
        return danmaku, comments, subtitle

    async def _capture_frames(self, video: Video) -> List[str]:
        """下载视频并抽帧，返回 data URL 列表。"""

        if self._frames is None or self._client is None or self._data_dir is None:
            return []
        if not self._frames.available:
            logger.debug("未找到 ffmpeg，跳过抽帧")
            return []
        work_dir = self._data_dir / "frames" / safe_bvid(getattr(video, "bvid", ""))
        try:
            frames = await self._frames.capture(self._client, video, work_dir)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("抽帧失败 %s：%s", video.bvid, exc)
            frames = []
        finally:
            cleanup_dir(work_dir)
        return frames

    # -- 抓取 ----------------------------------------------------------

    async def _fetch_candidates(
        self, keywords: Sequence[str]
    ) -> Tuple[List[Video], Dict[str, str]]:
        """按兴趣关键词与配置分类抓候选视频，返回 ``(视频列表, bvid→分类)``。"""

        config = self.config
        if self._client is None:
            raise BilibiliError("客户端尚未初始化")
        videos: List[Video] = []
        category_of: Dict[str, str] = {}
        attempts = 0
        failures = 0
        delay = max(0.0, float(config.sources.search_delay_seconds))

        async def _search(keyword: str, order: str, category: str) -> None:
            nonlocal attempts, failures
            keyword = (keyword or "").strip()
            if not keyword:
                return
            pages = max(1, int(config.sources.search_pages))
            for page in range(1, pages + 1):
                attempts += 1
                try:
                    found = await self._client.fetch_search(
                        keyword, page=page, order=order or ""
                    )
                except BilibiliError as exc:
                    failures += 1
                    logger.debug("搜索「%s」第 %s 页失败：%s", keyword, page, exc)
                    return
                for video in found:
                    videos.append(video)
                    # 先用普通关键词搜到的没有分类，后续按分类搜到时要补上
                    if category or video.bvid not in category_of:
                        category_of[video.bvid] = category
                if not found:
                    return
                if delay:
                    await asyncio.sleep(delay)

        if config.sources.search_enabled:
            for keyword in keywords:
                await _search(keyword, config.sources.search_order, "")
        for category in config.categories:
            if not category.enabled:
                continue
            for keyword in category.keywords or []:
                await _search(keyword, category.search_order, category.name)

        if config.sources.popular_enabled:
            for page in range(1, max(1, int(config.sources.popular_pages)) + 1):
                attempts += 1
                try:
                    found = await self._client.fetch_popular(
                        page_size=config.sources.popular_page_size, page=page
                    )
                except BilibiliError as exc:
                    failures += 1
                    logger.debug("抓取热门失败：%s", exc)
                    continue
                videos.extend(found)
            if delay:
                await asyncio.sleep(delay)

        if config.sources.ranking_enabled:
            for rid in config.sources.ranking_rids or [0]:
                attempts += 1
                try:
                    videos.extend(await self._client.fetch_ranking(rid=int(rid)))
                except BilibiliError as exc:
                    failures += 1
                    logger.debug("抓取排行榜失败：%s", exc)

        if attempts and failures == attempts and not videos:
            raise BilibiliError("所有 B 站接口都请求失败，请检查网络或 cookie")
        return merge_videos([videos]), category_of

    def _prefilter(self, videos: Sequence[Video]) -> List[Video]:
        """排除官方物料与过旧视频。"""

        config = self.config
        now = time.time()
        kept: List[Video] = []
        for video in videos:
            if config.filter.exclude_official and self._is_official(video):
                continue
            if config.filter.max_age_days > 0 and video.pubdate:
                age_days = max(0.0, (now - float(video.pubdate)) / 86400.0)
                if age_days > config.filter.max_age_days:
                    continue
            kept.append(video)
        return kept

    def _is_official(self, video: Video) -> bool:
        haystack = f"{video.title or ''} {video.description or ''}".lower()
        for keyword in self.config.filter.official_keywords or []:
            token = (keyword or "").strip().lower()
            if token and token in haystack:
                return True
        return False

    # -- 兴趣与关键词 --------------------------------------------------

    def _config_getter(self):
        """拿到宿主的 ``config.get`` 能力（拿不到时返回 None）。"""

        config_cap = getattr(self.ctx, "config", None)
        getter = getattr(config_cap, "get", None)
        return getter if callable(getter) else None

    def _effective_cookie(self) -> str:
        """当前生效的 B 站 Cookie：优先「账号登录」里的，其次旧版「高级」里的。"""

        config = self.config
        account = getattr(config, "account", None)
        cookie = str(getattr(account, "cookie", "") or "").strip()
        if cookie:
            return cookie
        return str(getattr(config.advanced, "cookie", "") or "").strip()

    def _config_file(self) -> Path:
        """插件自己的 ``config.toml`` 路径。"""

        return find_config_path(Path(__file__).resolve().parent)

    async def _refresh_allowed_streams(self) -> Set[str]:
        """解析白名单里所有启用的会话，缓存可用的 stream_id 集合。

        聊天里贴链接自动读这类被动行为只允许落在白名单会话里，
        这个集合就是判断依据。
        """

        config = self.config
        allowed: Set[str] = set()
        private: Set[str] = set()
        for target in config.streams or []:
            if not getattr(target, "enabled", False):
                continue
            stream_id = await self._resolve_stream(target)
            if stream_id:
                allowed.add(stream_id)
                is_private = bool(str(getattr(target, "user_id", "") or "").strip()) and not str(
                    getattr(target, "group_id", "") or ""
                ).strip()
                if is_private:
                    private.add(stream_id)
                for alternative in self._stream_fallbacks.get(stream_id, ()):
                    allowed.add(alternative)
                    if is_private:
                        private.add(alternative)
        self._allowed_stream_ids = allowed
        self._private_stream_ids = private
        self._allowed_streams_at = time.time()
        return allowed

    async def _refresh_bot_account_ids(self) -> Set[str]:
        """收集宿主里登记过的 bot 账号 id，用来过滤 bot 自己发的消息。"""

        chat = getattr(self.ctx, "chat", None)
        getter = getattr(chat, "get_all_streams", None) if chat is not None else None
        if not callable(getter):
            return self._bot_account_ids
        try:
            result = await getter("qq")
        except Exception as exc:  # noqa: BLE001
            logger.debug("枚举全部会话（收集 bot 账号）失败：%s", exc)
            return self._bot_account_ids
        streams = result.get("streams") if isinstance(result, dict) else result
        if not isinstance(streams, list):
            return self._bot_account_ids
        found: Set[str] = set()
        for item in streams:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("account_id") or "").strip()
            if account_id:
                found.add(account_id)
        if found:
            self._bot_account_ids = found
        return self._bot_account_ids

    async def _ensure_allowed_streams(self) -> Set[str]:
        """白名单集合为空或过期时重新解析。"""

        if self._allowed_stream_ids and (
            time.time() - self._allowed_streams_at
        ) < _ALLOWED_STREAMS_TTL_SECONDS:
            return self._allowed_stream_ids
        return await self._refresh_allowed_streams()

    async def _stream_candidates(self, target: Any, primary: str) -> List[str]:
        """列出同一个群 / 同一个用户下的所有会话。

        宿主换连接器 / 适配器之后，同一个群可能同时存在旧账号和新账号两套会话，
        而 ``get_stream_by_group_id`` 只返回其中一个（可能是已经掉线的旧会话）。
        这里把候选都收集起来，发送失败时好换下一个。
        """

        candidates: List[str] = []
        if primary:
            candidates.append(primary)
        chat = getattr(self.ctx, "chat", None)
        if chat is None:
            return candidates
        getter = getattr(chat, "get_all_streams", None)
        if not callable(getter):
            return candidates
        try:
            result = await getter("qq")
        except Exception as exc:  # noqa: BLE001
            logger.debug("枚举全部会话失败：%s", exc)
            return candidates
        streams = result.get("streams") if isinstance(result, dict) else result
        if not isinstance(streams, list):
            return candidates
        group_id = str(getattr(target, "group_id", "") or "").strip()
        user_id = str(getattr(target, "user_id", "") or "").strip()
        for item in streams:
            if not isinstance(item, dict):
                continue
            account_id = str(item.get("account_id") or "").strip()
            if account_id:
                self._bot_account_ids.add(account_id)
            stream_id = _stream_id_of(item)
            if not stream_id:
                continue
            if group_id:
                matched = str(item.get("group_id") or "").strip() == group_id
            elif user_id:
                matched = str(item.get("user_id") or "").strip() == user_id
            else:
                matched = False
            if matched and stream_id not in candidates:
                candidates.append(stream_id)
        return candidates

    async def _resolve_stream(self, target: Any) -> str:
        """把白名单配置解析成真实的 stream_id。

        同一群 / 用户可能对应多个会话（例如换连接器后旧账号的会话还在库里），
        这里把其余候选记到 ``_stream_fallbacks``，发送失败时会自动切换。
        """

        if target.stream_id:
            return str(target.stream_id).strip()
        chat = getattr(self.ctx, "chat", None)
        if chat is None:
            return ""
        primary = ""
        try:
            if target.group_id:
                stream = await chat.get_stream_by_group_id(str(target.group_id))
                primary = _stream_id_of(stream)
            if not primary and target.user_id:
                stream = await chat.get_stream_by_user_id(str(target.user_id))
                primary = _stream_id_of(stream)
        except Exception as exc:  # noqa: BLE001
            logger.warning("解析白名单会话失败（%s）：%s", target.name or target.group_id, exc)
        if not primary:
            return ""
        candidates = await self._stream_candidates(target, primary)
        alternatives = [item for item in candidates if item != primary]
        if alternatives:
            self._stream_fallbacks[primary] = alternatives
            logger.debug(
                "会话 %s 共有 %s 个候选会话（%s），发送失败时会自动切换",
                target.name or target.group_id,
                len(candidates),
                ", ".join(candidates),
            )
        return primary

    async def _build_profile(self, target: Any, stream_id: str) -> InterestProfile:
        """构建会话兴趣画像：配置里的显式兴趣 + 近期聊天关键词 + 人设初始兴趣。"""

        config = self.config
        profile = InterestProfile()
        for term in target.interests or []:
            normalized = (term or "").strip()
            if normalized:
                profile.add(normalized, config.interest.explicit_weight)

        auto_terms: List[str] = []
        if config.interest.use_recent_chat and target.auto_interest:
            messages = await self._recent_messages(stream_id)
            keywords = extract_interest_keywords(
                messages,
                top_n=config.interest.top_keywords,
                min_count=config.interest.keyword_min_count,
                extra_stopwords=config.interest.extra_stopwords,
            )
            persona_noise = self._persona_noise_terms()
            for term, count in keywords:
                if self._is_persona_noise(term, persona_noise):
                    # 群里叫 bot 的名字 / 别名不是「群的兴趣」，写回配置只会是噪音
                    continue
                weight = config.interest.auto_weight * min(
                    2.0, 0.6 + 0.4 * math.log2(1 + count)
                )
                profile.add(term, weight)
                auto_terms.append(term)
            if auto_terms:
                await self._persist_auto_interests(target, auto_terms)

        # 配置里已有自动兴趣（上一轮写回的），也参与打分
        for term in getattr(target, "auto_interests", None) or []:
            normalized = (term or "").strip()
            if normalized and not profile.has(normalized):
                profile.add(normalized, config.interest.auto_weight)

        # 一点兴趣都没有（既没手写、聊天里也提不出）→ 用通用的人设初始兴趣兜底
        if not profile.sorted_terms():
            seed = await self._ensure_persona_seed_interests()
            for term in seed:
                profile.add(term, config.interest.auto_weight)
        return profile

    def _persona_noise_terms(self) -> Set[str]:
        """人设名字与别名：群里喊 bot 的称呼不算「群的兴趣」。"""

        names: Set[str] = set()
        if self._persona is None:
            return names
        try:
            persona = self._persona.get()
        except Exception:  # noqa: BLE001 - 人设读不到就不做过滤
            return names
        if persona is None:
            return names
        raw_values = [getattr(persona, "name", "")]
        raw_values.extend(getattr(persona, "aliases", None) or [])
        for value in raw_values:
            normalized = normalize_term(str(value or ""))
            if normalized:
                names.add(normalized)
        return names

    @staticmethod
    def _is_persona_noise(term: str, noise: Set[str]) -> bool:
        """判断关键词是不是在喊 bot（人设名 / 别名）。"""

        normalized = normalize_term(str(term or ""))
        if not normalized:
            return True
        for item in noise:
            if not item:
                continue
            if normalized == item:
                return True
            # 太短的别名（单字）不做包含匹配，否则「鱼」会把「钓鱼」也误杀
            if len(item) >= 2 and (normalized in item or item in normalized):
                return True
        return False

    async def _persist_auto_interests(self, target: Any, terms: Sequence[str]) -> None:
        """把自动提取的兴趣记到待写队列，轮末再写回 ``config.toml``。

        用户手动改过就不再覆盖（相当于锁定）；清空则恢复自动维护。
        这里刻意不立刻写盘：宿主的 ``config.toml`` 一变就会重新加载插件，
        重新加载会把正在跑的这一轮直接打断（连分享都来不及发）。
        """

        config = self.config
        if not config.interest.persist_auto_interests or self._store is None:
            return
        key = stream_identity(target)
        if not key:
            return
        current = list(getattr(target, "auto_interests", None) or [])
        last_written = self._store.get_auto_interests(key)
        if not should_write_back(current, last_written):
            return
        cleaned = _normalize_terms(terms)
        # 词的先后顺序每轮可能不一样，按「集合」比较，避免内容没变却反复写盘
        if cleaned == _normalize_terms(current):
            return
        self._pending_stream_writes[key] = (target, cleaned)
        # 同步内存里的配置对象，本轮立刻生效（宿主不会因为文件变化重新加载配置）
        try:
            target.auto_interests = list(cleaned)
        except Exception:  # noqa: BLE001 - pydantic 模型通常是可写的
            pass

    async def _persona_seed_interests(self) -> List[str]:
        """还没有任何兴趣时，让 LLM 按 MaiBot 人设生成一份初始兴趣。"""

        config = self.config
        if not config.interest.persona_seed_enabled:
            return []
        if self._judge is None or not config.interest.use_llm_judge:
            logger.info(
                "跳过「人设初始兴趣」生成：需要同时开启 use_llm_judge 与 persona_seed_enabled"
            )
            return []
        persona_block = ""
        if self._persona is not None:
            try:
                persona_block = self._persona.prompt_block()
            except Exception:  # noqa: BLE001
                persona_block = ""
        try:
            return await self._judge.seed_interests(
                persona_block=persona_block,
                count=int(config.interest.persona_seed_count),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("用人设生成初始兴趣失败：%s", exc)
            return []

    def _configured_persona_seed(self) -> List[str]:
        """配置里已有的「人设初始兴趣」（空列表表示还没有）。"""

        return [
            str(item).strip()
            for item in (self.config.interest.persona_seed_interests or [])
            if str(item or "").strip()
        ]

    async def _ensure_persona_seed_interests(self) -> List[str]:
        """取「人设初始兴趣」这个通用配置；没有就按人设生成一份并写回配置。"""

        configured = self._configured_persona_seed()
        if configured:
            return configured
        seed = await self._persona_seed_interests()
        if seed:
            logger.info("已按人设生成初始兴趣：%s", "、".join(seed))
            self._persist_persona_seed_interests(seed)
        else:
            logger.info("还没有「人设初始兴趣」，本次按人设生成失败，下一轮再试")
        return seed

    def _persist_persona_seed_interests(self, terms: Sequence[str]) -> None:
        """把人设初始兴趣记到待写队列，轮末再写回 ``[interest].persona_seed_interests``。"""

        cleaned = [str(item).strip() for item in terms if str(item or "").strip()]
        if not cleaned:
            return
        self._pending_seed_terms = list(cleaned)
        # 同步内存里的配置对象，本轮立刻生效
        try:
            self.config.interest.persona_seed_interests = list(cleaned)
        except Exception:  # noqa: BLE001
            pass

    def _flush_pending_config_writes(self) -> None:
        """把本轮攒下的配置写回一次性落盘（放在轮末，避免打断当前这一轮）。"""

        if self._store is None:
            self._pending_stream_writes.clear()
            self._pending_seed_terms = []
            return
        if not self._pending_stream_writes and not self._pending_seed_terms:
            return
        path = self._config_file()
        wrote = False
        for key, (target, cleaned) in list(self._pending_stream_writes.items()):
            if update_stream_auto_interests(path, target, cleaned):
                self._store.set_auto_interests(key, cleaned)
                wrote = True
                logger.info(
                    "已把自动兴趣写回配置（%s）：%s",
                    getattr(target, "name", "") or key,
                    "、".join(cleaned),
                )
        if self._pending_seed_terms and update_persona_seed_interests(
            path, self._pending_seed_terms
        ):
            wrote = True
            logger.info(
                "已把人设初始兴趣写回配置：%s", "、".join(self._pending_seed_terms)
            )
        self._pending_stream_writes.clear()
        self._pending_seed_terms = []
        if not wrote:
            return
        # 记下「我们自己刚写过配置文件」：宿主的 config.toml 一变就会重新加载插件，
        # 重新加载又会跑一次启动轮，很容易变成「写配置 → 重载 → 又跑一轮 → 又写」的循环。
        self._store.set_meta(_CONFIG_WRITE_META, time.time())
        self._store.save()

    async def _recent_messages(self, stream_id: str) -> List[Dict[str, Any]]:
        """读取会话近期消息；主会话读不到就换同群/同用户的候选会话再试。

        宿主换连接器后，同一个群可能同时挂着旧账号的会话和新账号的会话，
        能力接口返回的往往是已经掉线的旧会话——它的消息停留在换账号之前，
        按 ``lookback_hours`` 过滤后是空的，会导致自动兴趣永远提不出来。
        这里按「记住的可用会话 → 主会话 → 其余候选」的顺序找第一个有消息的。
        """

        message_cap = getattr(self.ctx, "message", None)
        if message_cap is None or not stream_id:
            return []
        config = self.config
        primary = self._stream_overrides.get(stream_id, stream_id)
        candidates = [primary, stream_id]
        candidates.extend(self._stream_fallbacks.get(stream_id, ()) or ())
        tried: List[str] = []
        for candidate in candidates:
            if not candidate or candidate in tried:
                continue
            tried.append(candidate)
            messages = await self._recent_messages_of(message_cap, candidate)
            if messages:
                if candidate != stream_id and self._stream_overrides.get(stream_id) != candidate:
                    # 记住这个会话有消息，后续发送也优先用它
                    self._stream_overrides[stream_id] = candidate
                    logger.info(
                        "会话 %s 没有近期消息，改从候选会话 %s 读取聊天记录",
                        stream_id,
                        candidate,
                    )
                return messages
        return []

    async def _recent_messages_of(
        self, message_cap: Any, chat_id: str
    ) -> List[Dict[str, Any]]:
        """从指定会话读取近期消息（已按 ``lookback_hours`` 过滤）。

        读取时会尽量带上 ``filter_mai=True``：bot 自己发的分享消息里有大量
        「https://www.bilibili.com/video/BV...」「刚刷到」这类字样，混进兴趣提取
        里只会变成噪音。SDK 的 ``message.get_recent`` 没暴露这个参数，所以优先
        直接走 ``call_capability``，拿不到再退回普通调用。
        """

        config = self.config
        limit = int(config.interest.max_messages_per_stream)
        result = None
        call_capability = getattr(self.ctx, "call_capability", None)
        if callable(call_capability):
            try:
                result = await call_capability(
                    "message.get_recent",
                    chat_id=chat_id,
                    limit=limit,
                    filter_mai=True,
                )
            except Exception as exc:  # noqa: BLE001 - 老宿主可能不认这个参数
                logger.debug("带 filter_mai 读取会话 %s 消息失败，改用普通读取：%s", chat_id, exc)
                result = None
        if result is None:
            try:
                # 宿主 SDK 的 get_recent 只接受 chat_id / limit，时间窗需要自己过滤。
                result = await message_cap.get_recent(chat_id=chat_id, limit=limit)
            except Exception as exc:  # noqa: BLE001
                logger.warning("读取会话 %s 近期消息失败：%s", chat_id, exc)
                return []
        if isinstance(result, dict):
            messages = result.get("messages")
            messages = list(messages) if isinstance(messages, list) else []
        else:
            messages = list(result or [])
        messages = [item for item in messages if not self._is_ignored_message(item)]
        return filter_messages_by_lookback(
            messages, float(config.interest.lookback_hours)
        )

    def _is_ignored_message(self, message: Any) -> bool:
        """这条消息要不要排除在兴趣提取之外。

        排除三类：自己发的消息、转发的 B 站视频链接、
        以及用户在「不参与提取的账号」里列出的账号。

        其他 bot 不做自动识别：昵称里带人设名字这类启发式太容易误伤真人，
        所以只保留 ``exclude_user_ids`` 这个手动开关。
        """

        if not isinstance(message, dict):
            return True
        if _looks_like_own_share(message):
            return True
        user_id = _message_user_id(message)
        if not user_id:
            return False
        if user_id in self._bot_account_ids:
            return True
        config = self.config
        excluded = {
            str(item).strip() for item in (config.interest.exclude_user_ids or []) if str(item or "").strip()
        }
        return user_id in excluded

    def _collect_search_keywords(self, profiles: Sequence[InterestProfile]) -> List[str]:
        """按出现频次从各会话画像里挑出本轮搜索关键词。"""

        counter: Dict[str, float] = {}
        for profile in profiles:
            for term, weight in profile.sorted_terms():
                counter[term] = counter.get(term, 0.0) + float(weight)
        ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
        limit = max(1, int(self.config.sources.search_keywords_per_round))
        return [term for term, _weight in ranked[:limit]]

    # -- 发送 ----------------------------------------------------------

    async def _send_text(self, stream_id: str, text: str) -> bool:
        """发送一条文本；失败时自动换用同群 / 同用户的其他候选会话。

        宿主换连接器后，同一个群可能同时挂着旧账号的会话和新账号的会话，
        能力接口返回的可能正是那个已经掉线的旧会话（发送时报「未命中任何发送路由」）。
        这种情况下换一个候选会话重试，并把可用的那个记下来。
        """

        text = (text or "").strip()
        if not text or not stream_id:
            return False
        primary = self._stream_overrides.get(stream_id, stream_id)
        if await self._send_text_once(primary, text):
            return True
        for alternative in self._stream_fallbacks.get(stream_id, ()) or ():
            if alternative == primary:
                continue
            if await self._send_text_once(alternative, text):
                self._stream_overrides[stream_id] = alternative
                logger.info(
                    "会话 %s 发送失败，已自动改用可用会话 %s",
                    stream_id,
                    alternative,
                )
                return True
        return False

    async def _send_text_once(self, stream_id: str, text: str) -> bool:
        """向指定会话发送一条文本；``stream_id`` 为空时返回 False。"""

        text = (text or "").strip()
        if not text or not stream_id:
            return False
        send = getattr(self.ctx, "send", None)
        if send is None:
            return False
        try:
            result = await send.text(text, stream_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("发送消息失败（%s）：%s", stream_id, exc)
            return False
        if isinstance(result, dict):
            # 不同宿主版本的返回结构不同：
            # - 宿主 1.3.15 / SDK 2.16（return_details=False）：{"success": bool}
            # - 旧版或 return_details=True：{"sent": bool, "message_id": ...}
            if "sent" in result:
                return bool(result.get("sent"))
            if "success" in result:
                if not result.get("success"):
                    logger.warning(
                        "发送消息失败（%s）：%s", stream_id, result.get("error") or result
                    )
                    return False
                return True
            return True
        return bool(result)

    async def _send_parts(
        self,
        stream_id: str,
        video: Video,
        narrative: str,
        *,
        kind: str = "auto",
    ) -> bool:
        """发送分享消息：一句随口的话 + 【标题】链接，合成一条。

        赤石（kind="shiwei"）不带文案，只发标题链接。
        """

        config = self.config
        line = "" if kind == "shiwei" else (narrative or "").strip()
        header = format_share_header(video)
        message = f"{line} {header}" if line else header
        if config.share.include_stats_line:
            message += (
                f"\n🔥 {video.play} 播放 · {video.like} 点赞"
                + (f" · UP：{video.owner}" if video.owner else "")
            )
        return await self._send_text(stream_id, message)

    # -- 主动搜索 ------------------------------------------------------

    async def search_and_share(
        self, query: str, stream_id: str, *, limit: int = 1
    ) -> Dict[str, Any]:
        """响应用户要求主动搜索并分享（有独立冷却）。"""

        config = self.config
        query = (query or "").strip()
        if not query:
            return {"ok": False, "reason": "empty_query"}
        if not config.plugin.enabled:
            return {"ok": False, "reason": "disabled"}
        if self._client is None or self._store is None:
            return {"ok": False, "reason": "not_loaded"}
        # 主动搜索也只在白名单会话里生效，避免任意群里一句话就让模型把视频发过去
        allowed = await self._ensure_allowed_streams()
        if stream_id not in allowed:
            logger.debug("会话 %s 不在白名单，忽略主动搜索请求", stream_id)
            return {"ok": False, "reason": "not_allowed"}

        remaining = self._store.cooldown_remaining(
            COOLDOWN_ACTIVE_SEARCH, config.schedule.search_cooldown_seconds
        )
        if remaining > 0:
            return {"ok": False, "reason": "cooldown", "remaining": int(remaining)}

        try:
            found = await self._client.fetch_search(query, page=1, order=config.sources.search_order)
        except BilibiliError as exc:
            return {"ok": False, "reason": "fetch_failed", "error": str(exc)}
        videos = self._prefilter(found)
        if not videos:
            return {"ok": False, "reason": "no_candidates"}

        profile = InterestProfile()
        profile.add(query, config.interest.explicit_weight)
        results = rank_videos(
            videos,
            profile,
            now=time.time(),
            min_score=0.0,
            require_interest_match=False,
            interest_weight=config.scoring.interest_weight,
            title_match_boost=config.scoring.title_match_boost,
            hot_weight=config.scoring.hot_weight,
            freshness_weight=config.scoring.freshness_weight,
            freshness_days=config.scoring.freshness_days,
            min_duration=config.filter.min_duration_seconds,
            max_duration=config.filter.max_duration_seconds,
            min_play=config.filter.min_play,
            min_like=config.filter.min_like,
            block_keywords=config.filter.block_keywords,
            block_up_names=config.filter.block_up_names,
            exclude_partitions=config.filter.exclude_partitions,
            use_tags=config.interest.use_tags,
            tag_weight=config.interest.tag_weight,
        )
        if not results:
            return {"ok": False, "reason": "filtered_out"}

        shared: List[str] = []
        for result in results[: max(1, int(limit))]:
            video = result.video
            cached = self._store.get(video.bvid)
            if cached and (cached.get("narrative") or cached.get("summary")):
                # 名单内视频再次分享：直接复用之前的介绍文案，不重新生成
                await self._send_parts(
                    stream_id,
                    video,
                    str(cached.get("narrative") or cached.get("summary") or ""),
                    kind="search",
                )
                shared.append(video.bvid)
                continue
            danmaku, comments, subtitle = await self._collect_detail(video)
            judged = None
            if self._judge is not None and self._judge.available:
                judged = await self._judge.judge(
                    video,
                    interests=[query],
                    danmaku=danmaku,
                    comments=comments,
                    subtitle=subtitle,
                    casual_max_chars=config.share.casual_max_chars,
                )
            narrative_text = str((judged or {}).get("narrative") or "") or (
                video.description or ""
            )[: int(config.share.casual_max_chars)]
            if await self._send_parts(
                stream_id,
                video,
                narrative_text,
                kind="search",
            ):
                self._store.mark_shared(
                    video,
                    narrative=narrative_text,
                    kind="search",
                    stream_id=stream_id,
                )
                shared.append(video.bvid)

        if shared:
            self._store.mark_cooldown(COOLDOWN_ACTIVE_SEARCH)
            self._store.save()
        return {"ok": bool(shared), "shared": shared, "query": query}

    async def share_shiwei(self, stream_id: str) -> Dict[str, Any]:
        """``/赤石视频``：抓取并分享抽象整活类视频，不带总结和看点。"""

        config = self.config
        if not config.plugin.enabled:
            return {"ok": False, "reason": "disabled"}
        if not config.shiwei.enabled:
            return {"ok": False, "reason": "disabled"}
        if self._client is None or self._store is None:
            return {"ok": False, "reason": "not_loaded"}
        # 赤石视频同样只发给白名单会话
        allowed = await self._ensure_allowed_streams()
        if stream_id not in allowed:
            logger.debug("会话 %s 不在白名单，忽略赤石视频请求", stream_id)
            return {"ok": False, "reason": "not_allowed"}

        remaining = self._store.cooldown_remaining(COOLDOWN_SHIWEI, config.shiwei.cooldown_seconds)
        if remaining > 0:
            return {"ok": False, "reason": "cooldown", "remaining": int(remaining)}

        keywords = [item for item in config.shiwei.keywords if item][
            : max(1, int(config.shiwei.search_keywords_per_round))
        ]
        videos: List[Video] = []
        for keyword in keywords:
            try:
                videos.extend(await self._client.fetch_search(keyword, page=1))
            except BilibiliError as exc:
                logger.debug("赤石搜索「%s」失败：%s", keyword, exc)
            if config.sources.search_delay_seconds > 0:
                await asyncio.sleep(float(config.sources.search_delay_seconds))
        if not videos:
            return {"ok": False, "reason": "no_candidates"}

        blocked = [item.lower() for item in config.shiwei.block_keywords or []]
        # 赤石路径也套一层全局屏蔽词，避免把广告/带货/官方预告当整活发出去
        blocked.extend(item.lower() for item in config.filter.block_keywords or [])
        if config.filter.exclude_official:
            blocked.extend(item.lower() for item in config.filter.official_keywords or [])
        blocked_up = [item.lower() for item in config.filter.block_up_names or []]
        shiwei_words = [item for item in config.shiwei.keywords if item]
        scored: List[Tuple[float, Video]] = []
        for video in merge_videos([videos]):
            haystack = f"{video.title} {video.description} {getattr(video, 'tags', '')}".lower()
            if any(token and token in haystack for token in blocked):
                continue
            if blocked_up and any(name and name in str(getattr(video, "owner", "")).lower() for name in blocked_up):
                continue
            if video.play < config.shiwei.min_play:
                continue
            if video.duration < config.shiwei.min_duration_seconds:
                continue
            if video.duration > config.shiwei.max_duration_seconds:
                continue
            hits = sum(1 for word in shiwei_words if word.lower() in haystack)
            if not hits:
                continue
            scored.append((hits * 2.0 + math.log10(1 + video.play) * 0.3, video))
        if not scored:
            return {"ok": False, "reason": "no_candidates"}
        scored.sort(key=lambda item: -item[0])
        shared: List[str] = []
        for _score, video in scored[: max(1, int(config.shiwei.max_share))]:
            if await self._send_parts(stream_id, video, "", kind="shiwei"):
                self._store.mark_shared(video, kind="shiwei", stream_id=stream_id)
                shared.append(video.bvid)
        if shared:
            self._store.mark_cooldown(COOLDOWN_SHIWEI)
            self._store.save()
        return {"ok": bool(shared), "shared": shared}

    # -- 读视频（bili_read 工具 / 贴链接自动读） ------------------------

    def _ensure_client(self) -> BilibiliClient:
        """返回可用的 B 站客户端；还没跑过 on_load 时按当前配置临时补建。"""

        client = self._client
        if client is None:
            config = self.config
            client = BilibiliClient(
                timeout=config.advanced.request_timeout_seconds,
                cookie=self._effective_cookie(),
                user_agent=config.advanced.user_agent,
            )
            self._client = client
        return client

    def _ensure_read_cache(self) -> VideoCache:
        cache = self._read_cache
        if cache is None:
            cache = VideoCache(ttl_seconds=int(self.config.read.cache_seconds))
            self._read_cache = cache
        return cache

    async def read_video(
        self, raw: str, *, max_chars: int = 0, comment_limit: int = -1, detail: str = ""
    ) -> Tuple[VideoBrief, str]:
        """读一条视频，返回（结构化资料, 给模型看的文本）。

        detail="full" 时走深度读：完整字幕时间轴、按分钟聚合的弹幕时间线、
        更多热评，正文上限提到 DEEP_READ_MAX_CHARS。
        """

        target = parse_target(raw)
        if target is None:
            raise BiliReadError("没认出 B 站链接或 BV 号")

        config = self.config
        deep = normalize_detail(detail) == "full"
        if deep:
            resolved_comment_limit = (
                int(comment_limit) if comment_limit >= 0 else DEEP_READ_COMMENT_LIMIT
            )
            resolved_danmaku_limit = DEEP_READ_DANMAKU_LIMIT
            resolved_subtitle_chars = DEEP_READ_SUBTITLE_CHARS
            resolved_max_chars = int(max_chars or DEEP_READ_MAX_CHARS)
        else:
            resolved_comment_limit = (
                int(comment_limit) if comment_limit >= 0 else int(config.read.comment_limit)
            )
            resolved_danmaku_limit = int(config.read.danmaku_limit)
            resolved_subtitle_chars = 1200  # 与 read_brief 的默认值保持一致
            resolved_max_chars = int(max_chars or config.read.max_chars)
        brief = await read_brief(
            self._ensure_client(),
            target,
            comment_limit=resolved_comment_limit,
            danmaku_limit=resolved_danmaku_limit,
            use_subtitle=bool(config.read.use_subtitle),
            subtitle_chars=resolved_subtitle_chars,
            cache=self._ensure_read_cache(),
        )
        return brief, render_brief(
            brief, max_chars=resolved_max_chars, detail="full" if deep else "brief"
        )

    @Tool(
        "bili_read",
        description=(
            "读取一个 B 站视频的真实内容：标题、UP 主、分区、时长、简介、分P、标签、"
            "CC 字幕、热门评论与弹幕采样。用户贴了 B 站链接、或者问「这个视频讲了什么」"
            "「值不值得看」时，先调用它拿到内容，不要凭标题猜。"
            "它一次调用通常 1 秒内返回，在当前对话里直接调用即可，不需要交给子代理；"
            "如果上下文里已经有这个视频的读取结果，就不用重复读。"
            "想看细节（逐段讲了什么、哪里有高能）时传 detail=full，会返回字幕时间轴、弹幕时间线；"
            "视觉模型可用时还会抽帧看画面，把画面描述一起返回。"
        ),
        parameters=[
            ToolParameterInfo(
                name="url",
                param_type=ToolParamType.STRING,
                description="B 站视频链接、BV 号（如 BV1xx411c7mD）或 av 号",
                required=True,
            ),
            ToolParameterInfo(
                name="detail",
                param_type=ToolParamType.STRING,
                description=(
                    "读取深度：brief（默认，快速摘要，省 token）/ full（深度读："
                    "完整字幕时间轴、弹幕时间线、更多热评，正文更长）。"
                    "要细看视频内容、做学习笔记、或摘要里明显缺细节时传 full。"
                ),
                required=False,
            ),
        ],
        chat_scope="all",
        # 让 Planner 直接看见这个工具（默认 deferred 要先 tool_search，模型就会
        # 转而派子代理 + 每 30 秒 wait 一次）。列表里多一条 schema，比空转便宜得多。
        visibility="visible",
        core_tool=True,
    )
    async def tool_bili_read(self, url: str = "", **kwargs: Any) -> Dict[str, Any]:
        target_text = (url or "").strip()
        if not target_text:
            for key in ("target", "link", "bv", "text", "query"):
                value = kwargs.get(key)
                if isinstance(value, str) and value.strip():
                    target_text = value.strip()
                    break
        if not target_text:
            return {"success": False, "error": "缺少视频链接或 BV 号"}
        try:
            if not self.config.read.enabled:
                return {"success": False, "error": "读视频功能已在插件配置的「读视频」里关闭"}
        except Exception:  # noqa: BLE001 - 配置还没注入时按启用处理
            pass
        detail = normalize_detail(kwargs.get("detail"))
        try:
            brief, content = await self.read_video(target_text, detail=detail)
        except asyncio.CancelledError:
            raise
        except BiliReadError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 工具里不能抛错
            logger.warning("bili_read 读取失败：%s", exc, exc_info=self._debug_enabled())
            return {"success": False, "error": f"读取视频失败：{exc}"}
        if detail == "full":
            vision_text = await self._describe_video_frames(brief)
            if vision_text:
                content = f"{content}\n\n【画面描述（抽帧）】\n{vision_text}"
        logger.info("bili_read 读取成功：%s（%s）", brief.title, brief.bvid)
        return {
            "success": True,
            "content": content,
            "title": brief.title,
            "bvid": brief.bvid,
            "url": brief.url,
        }

    async def _append_read_context(
        self, stream_id: str, brief: VideoBrief, text: str, *, deep: bool = False
    ) -> bool:
        """把读到的视频内容写进会话上下文，让麦麦接下来的回复能自然用上。"""

        try:
            maisaka = getattr(self.ctx, "maisaka", None)
        except Exception:  # noqa: BLE001 - 上下文还没注入
            return False
        context = getattr(maisaka, "context", None)
        if context is None:
            return False
        depth = "深度读（含字幕时间轴、弹幕时间线和画面描述）" if deep else "快速读"
        content = (
            f"（下面是我刚{depth}到的 B 站视频内容，已经放进上下文，可以自然地聊起：{brief.title}；"
            f"直接用这份内容回答即可，不用再调用 bili_read）\n{text}"
        )
        try:
            await context.append(
                stream_id=stream_id,
                segments=[{"type": "text", "content": content}],
                visible_text=f"已读取 B 站视频：{brief.title}",
                source_kind="plugin:bilibili_interest_share",
            )
        except Exception as exc:  # noqa: BLE001 - 写上下文失败不影响读视频
            logger.debug("写入视频上下文失败：%s", exc)
            return False
        return True

    async def _stream_scope_for_message(self, stream_id: str, message: Any) -> str:
        """判断消息所在会话是不是白名单里的启用会话。

        返回 "group" / "private" / ""。除了核对已解析的 stream_id，
        还会拿消息里的群号 / 用户号去比配置——新群第一条消息时宿主
        可能还没来得及登记会话，这时 stream_id 是查不到的。
        """

        config = self.config
        group_id = _message_group_id(message)
        user_id = _message_user_id(message)
        matched = None
        for target in config.streams or []:
            if not getattr(target, "enabled", False):
                continue
            target_group = str(getattr(target, "group_id", "") or "").strip()
            target_user = str(getattr(target, "user_id", "") or "").strip()
            if target_group and group_id and target_group == group_id:
                matched = target
                break
            if target_user and not group_id and target_user == user_id:
                matched = target
                break
        if matched is not None:
            is_private = bool(
                str(getattr(matched, "user_id", "") or "").strip()
            ) and not str(getattr(matched, "group_id", "") or "").strip()
            return "private" if is_private else "group"
        allowed = await self._ensure_allowed_streams()
        if stream_id in allowed:
            return "private" if stream_id in self._private_stream_ids else "group"
        return ""

    async def _maybe_auto_read(
        self, message: Any, kwargs: Optional[Dict[str, Any]] = None
    ) -> None:
        """白名单会话里有人贴 B 站链接时，自动读一遍塞进上下文。"""

        config = self.config
        if not config.plugin.enabled or not config.read.enabled:
            return
        if not config.read.auto_read_enabled:
            return
        text = _message_link_text(message)
        if not text or text.startswith("【"):  # 跳过插件自己发的分享消息
            return
        targets = extract_targets(text, limit=int(config.read.auto_read_max_links))
        if not targets:
            return
        stream_id = _stream_id_of(message)
        if not stream_id and isinstance(kwargs, dict):
            for key in ("stream_id", "session_id"):
                value = kwargs.get(key)
                if isinstance(value, str) and value.strip():
                    stream_id = value.strip()
                    break
        if not stream_id:
            logger.debug("自动读：消息里取不到会话 id，跳过")
            return
        scope = await self._stream_scope_for_message(stream_id, message)
        if not scope:
            logger.debug("会话 %s 不在白名单，跳过自动读视频", stream_id)
            return

        deep = wants_full_read(message, text, config.read.full_trigger_keywords)
        # 私聊里没有 @ 这种操作，对着麦麦贴链接本身就是「特意让我看」，默认深度读
        if (
            not deep
            and getattr(config.read, "full_in_private", False)
            and scope == "private"
        ):
            deep = True
        mode = "full" if deep else "brief"
        now = time.time()
        window = max(60.0, float(config.read.cache_seconds))
        if len(self._auto_read_seen) > 500:
            self._auto_read_seen = {
                key: stamp for key, stamp in self._auto_read_seen.items() if now - stamp < 86400.0
            }
        appended = 0
        for item in targets:
            target = parse_target(item)
            if target is None:
                continue
            seen_key = f"{stream_id}|{target.key}|{mode}"
            if now - self._auto_read_seen.get(seen_key, 0.0) < window:
                logger.debug("视频 %s 刚%s读过，跳过", target.key, "深度" if deep else "快速")
                continue
            try:
                if deep:
                    brief, brief_text = await asyncio.wait_for(
                        self.read_video(item, detail="full"), timeout=90.0
                    )
                    vision_text = await self._describe_video_frames(brief)
                    if vision_text:
                        brief_text = (
                            f"{brief_text}\n\n【画面描述（抽帧）】\n{vision_text}"
                        )
                else:
                    brief, brief_text = await asyncio.wait_for(self.read_video(item), timeout=20.0)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 自动读失败只是一条日志
                logger.debug("自动读视频失败（%s）：%s", item, exc)
                continue
            self._auto_read_seen[seen_key] = now
            if await self._append_read_context(stream_id, brief, brief_text, deep=deep):
                appended += 1
                logger.info(
                    "自动%s读取 B 站视频：%s（%s）",
                    "深度" if deep else "快速",
                    brief.title,
                    brief.bvid,
                )
        if appended:
            logger.debug("会话 %s 本轮自动读取 %s 个视频", stream_id, appended)

    async def _describe_video_frames(self, brief: VideoBrief) -> str:
        """full 模式下抽几帧交给视觉模型，返回画面描述（任何一步失败都返回空串）。"""

        config = self.config
        if not config.read.full_vision_enabled or not config.vision.enabled:
            return ""
        cache_key = str(brief.bvid or "").strip()
        if cache_key:
            cached = self._vision_cache.get(cache_key)
            if cached and time.time() - cached[0] < float(config.read.cache_seconds):
                return cached[1]
        judge = self._judge
        if judge is None or not getattr(judge, "available", False):
            logger.debug("视觉模型不可用，跳过 full 模式的画面描述")
            return ""
        video = Video(
            bvid=brief.bvid,
            aid=brief.aid,
            title=brief.title,
            owner=brief.owner,
            partition=brief.partition,
            duration=brief.duration,
            pubdate=brief.pubdate,
        )
        frames = await self._capture_frames(video)
        if not frames:
            logger.debug("没有抽到画面，full 模式只返回文字内容")
            return ""
        try:
            description = await asyncio.wait_for(
                judge.describe_frames(video, frames),
                timeout=float(config.vision.timeout_seconds),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 画面描述失败不影响文字内容
            logger.debug("画面描述失败 %s：%s", brief.bvid, exc)
            return ""
        if description and cache_key:
            self._vision_cache[cache_key] = (time.time(), description)
        return description

    # -- 聊天消息：贴链接自动读 ----------------------------------------

    @HookHandler(
        "chat.receive.after_process",
        mode=HookMode.OBSERVE,
        description="群友贴 B 站链接时自动读一遍，把标题/简介/弹幕/评论塞进上下文",
        timeout_ms=180000,
        error_policy=ErrorPolicy.LOG,
    )
    async def on_chat_receive(
        self, message: Any = None, **kwargs: Any
    ) -> None:
        try:
            await self._maybe_auto_read(message, kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - Hook 里不能抛错
            logger.debug("自动读视频失败：%s", exc)
        return None

    # -- 工具：主动搜索 / 赤石（聊天指令会被平台拦截，改由模型调用工具） --

    def _stream_from_tool_kwargs(self, kwargs: Dict[str, Any]) -> str:
        """工具调用里取当前聊天流：优先顶层 stream_id，其次宿主的 session 对象。"""

        stream_id = _stream_id_of(kwargs)
        if stream_id:
            return stream_id
        for key in ("session", "message", "event"):
            value = kwargs.get(key)
            if isinstance(value, dict):
                found = _stream_id_of(value) or _stream_id_of(value.get("message_info") or {})
                if found:
                    return found
        return ""

    @Tool(
        "bili_search",
        description=(
            "按关键词搜索 B 站视频，并把最相关的一条分享到当前聊天。"
            "用户说「搜点 XX 视频」「找个 XX 的 B 站视频发群里」时用它；"
            "工具会自己完成搜索、过滤、打分和发送，不需要再调用 send。"
            "只能在插件白名单聊天流里使用。"
        ),
        parameters=[
            ToolParameterInfo(
                name="query",
                param_type=ToolParamType.STRING,
                description="搜索关键词（例如「原神 新版本」）",
                required=True,
            ),
            ToolParameterInfo(
                name="count",
                param_type=ToolParamType.INTEGER,
                description="分享几条（默认 1，最多 3）",
                required=False,
            ),
        ],
        chat_scope="all",
        visibility="visible",
        core_tool=True,
    )
    async def tool_bili_search(
        self, query: str = "", count: int = 1, **kwargs: Any
    ) -> Dict[str, Any]:
        stream_id = self._stream_from_tool_kwargs(kwargs)
        if not stream_id:
            return {"success": False, "error": "需要在聊天里调用这个工具"}
        query = (query or "").strip()
        if not query:
            return {"success": False, "error": "缺少搜索关键词"}
        try:
            limit = max(1, min(3, int(count or 1)))
        except (TypeError, ValueError):
            limit = 1
        result = await self.search_and_share(query, stream_id, limit=limit)
        if result.get("ok"):
            shared = list(result.get("shared") or [])
            return {
                "success": True,
                "content": f"已搜索并分享 {len(shared)} 条：{query}",
                "shared": shared,
            }
        reason = str(result.get("reason") or "")
        messages = {
            "not_allowed": "这个会话不在插件白名单里，不能主动分享",
            "cooldown": f"主动搜索还在冷却中，{int(result.get('remaining') or 0)} 秒后再试",
            "disabled": "插件已禁用",
            "no_candidates": "没搜到合适的视频",
            "fetch_failed": f"搜索失败：{result.get('error') or ''}",
        }
        return {"success": False, "error": messages.get(reason, f"主动搜索没有成功：{reason}")}

    @Tool(
        "bili_shiwei",
        description=(
            "抓一个抽象整活类（「赤石」）B 站视频，原样分享到当前聊天，不带总结。"
            "用户想看点离谱、整活的视频时用它；工具会自己完成搜索和发送，"
            "不需要再调用 send。只能在插件白名单聊天流里使用。"
        ),
        chat_scope="all",
        visibility="visible",
        core_tool=True,
    )
    async def tool_bili_shiwei(self, **kwargs: Any) -> Dict[str, Any]:
        stream_id = self._stream_from_tool_kwargs(kwargs)
        if not stream_id:
            return {"success": False, "error": "需要在聊天里调用这个工具"}
        result = await self.share_shiwei(stream_id)
        if result.get("ok"):
            return {
                "success": True,
                "content": "赤石视频已送达",
                "shared": list(result.get("shared") or []),
            }
        reason = str(result.get("reason") or "")
        messages = {
            "not_allowed": "这个会话不在插件白名单里，不能主动分享",
            "cooldown": f"赤石视频还在冷却中，{int(result.get('remaining') or 0)} 秒后再试",
            "no_candidates": "这次没捞到合适的赤石视频",
        }
        return {
            "success": False,
            "error": messages.get(reason, f"这次没捞到合适的赤石视频（{reason}）"),
        }

    # -- 杂项 ----------------------------------------------------------

    def _resolve_data_dir(self) -> Path:
        config = self.config
        root = self._default_data_root()
        configured = (config.advanced.data_dir or "").strip()
        if not configured:
            return root
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = root / path
        return path

    def _default_data_root(self) -> Path:
        """本插件自己的数据目录。

        宿主注入的 ``ctx.paths.data_dir`` 已经是 ``data/plugins/<plugin_id>``，
        这里不能再拼一次插件 ID（早期版本这么写，会多套一层目录，
        导致 state.json 落在 ``data/plugins/<id>/<id>/`` 下）。
        """
        paths = getattr(self.ctx, "paths", None)
        data_dir = getattr(paths, "data_dir", None) if paths is not None else None
        if data_dir:
            try:
                path = Path(data_dir)
            except TypeError:
                path = None
            if path is not None:
                if path.name != PLUGIN_ID:
                    path = path / PLUGIN_ID
                return path
        return Path.cwd() / "data" / "plugins" / PLUGIN_ID

    def _debug_enabled(self) -> bool:
        try:
            return bool(self.config.advanced.debug)
        except Exception:  # noqa: BLE001
            return False


def create_plugin() -> BilibiliInterestSharePlugin:
    """宿主加载入口。"""

    return BilibiliInterestSharePlugin()
