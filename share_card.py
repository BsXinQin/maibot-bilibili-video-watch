"""QQ 小程序卡片（OneBot ``json`` 段）的构造与直发。

麦麦主程序和它自带的 QQ 适配器（SnowLuma / NapCat）都会把 OneBot 的
``json`` 段降级成 ``[unsupported:json]`` 纯文本，所以想让分享真的以
小程序卡片的形式出现，只能由插件自己 POST 到 QQ 客户端的 OneBot HTTP
接口（NapCat 默认 3000 端口，SnowLuma 同理）。

这个模块刻意只依赖 httpx：不碰麦麦 SDK，方便单测与复用。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("plugin.bilibili_interest_share.share_card")

# B 站小程序的 AppID（手机上把 B 站视频转发给好友时用的就是它）
BILIBILI_APPID = 1109937557
# 卡片外壳：外层「QQ 小程序」容器的 AppID（不是 B 站那个，取自手机真实卡片）
BILIBILI_MINIAPP_APPID = "100951776"
# B 站小程序分享模板 ID；卡片里的 view 就由它拼成 view_<模板ID>
BILIBILI_MINIAPP_SHARE_TEMPLATE_ID = "8C8E89B49BE609866298ADDFF2DBABA4"
BILIBILI_MINIAPP_VIEW = "view_" + BILIBILI_MINIAPP_SHARE_TEMPLATE_ID
# 卡片左侧小图标（腾讯小程序图标 CDN，手机卡片用的就是这张）
BILIBILI_MINIAPP_ICON = (
    "http://miniapp.gtimg.cn/public/appicon/432b76be3a548fc128acaa6c1ec90131_200.jpg"
)
# 分享场景值，手机卡片固定 1036
BILIBILI_MINIAPP_SCENE = 1036
# 合法的模板 view，形如 view_8C8E89B4...
_VIEW_RE = re.compile(r"^view_[0-9A-Fa-f]{16,40}$")

# 通用小图标；拿不到也不影响卡片显示
BILIBILI_ICON = "https://www.bilibili.com/favicon.ico"
# 标题/简介的长度上限（太长 QQ 会截断，先自己截干净）
MAX_TITLE_CHARS = 60
MAX_DESC_CHARS = 80
# SnowLuma 专有动作：用官方 oidb 通道现生成一张真卡片（NapCat 没有）
MINIAPP_ARK_ACTION = "get_mini_app_ark"
# SnowLuma 专有动作：发原生「图文 Ark」卡片（不依赖小程序，任何 QQ 版本都能渲染）
TUWEN_ARK_ACTION = "send_tuwen_ark"
# QQ 把卡片丢掉的证据：它会用这些文案顶替原来那条消息
DEGRADED_MARKERS = (
    "发送者版本过低",
    "版本过低",
    "无法展示内容",
    "[unsupported",
    "请升级到最新版",
    "请使用最新版本",
)


def normalize_cover(url: str) -> str:
    """把 B 站返回的封面地址修成可直接引用的 http(s) 地址。

    B 站接口常返回 ``//i0.hdslb.com/...`` 这种协议相对地址。
    """

    text = (url or "").strip()
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("http://"):
        return "https://" + text[len("http://"):]
    if text.startswith("https://"):
        return text
    return ""


def clean_text(value: Any, limit: int) -> str:
    """去掉换行与多余空白，并按字数截断。"""

    text = str(value or "")
    for chunk in ("\r\n", "\r", "\n", "\t"):
        text = text.replace(chunk, " ")
    text = " ".join(text.split())
    if limit > 0 and len(text) > limit:
        text = text[: max(1, limit - 1)].rstrip() + "…"
    return text


def _share_path_hash(text: str) -> str:
    """给卡片里的 m.q.qq.com/a/s/<32位> 跳转路径生成稳定后缀。

    手机端的这个串由服务端下发，本地只能造一个稳定值；客户端真正跳转用的是
    qqdocurl，所以做摘要即可。
    """

    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def build_miniapp_payload(
    *,
    title: str,
    jump_url: str,
    cover: str = "",
    desc: str = "",
    tag: str = "哔哩哔哩",
    pub_time: int = 0,
    icon: str = BILIBILI_MINIAPP_ICON,
    host_uin: Any = 0,
    host_nick: str = "",
) -> Dict[str, Any]:
    """按手机上那张真卡片的结构拼 1 张「B 站小程序」卡片。

    关键点都是从真实卡片里抄的：外层 appID、view_<分享模板ID>、
    config.forward、bthirdappforward，以及 meta.detail_1 的
    appid / shareTemplateId / qqdocurl。少任何一项 QQ 都可能
    把它当非法卡片丢掉（表现为「小程序已下架」）。

    缺标题或链接时返回 {}——调用方据此退回图文。
    """

    clean_title = clean_text(title, MAX_TITLE_CHARS)
    clean_url = (jump_url or "").strip()
    if not clean_title or not clean_url:
        return {}
    clean_cover = normalize_cover(cover)
    clean_tag = clean_text(tag, MAX_TITLE_CHARS) or "哔哩哔哩"
    ctime = _safe_int(pub_time)

    detail: Dict[str, Any] = {
        "appid": str(BILIBILI_APPID),
        "preview": clean_cover,
        "shareTemplateData": {},
        "gamePointsUrl": "",
        "gamePoints": "",
        "url": "m.q.qq.com/a/s/" + _share_path_hash(clean_url),
        "appType": 0,
        "desc": clean_text(desc or clean_title, MAX_DESC_CHARS),
        "title": clean_tag,
        "scene": BILIBILI_MINIAPP_SCENE,
        "host": {"uin": _safe_int(host_uin), "nick": clean_text(host_nick, 40)},
        "icon": icon,
        "shareTemplateId": BILIBILI_MINIAPP_SHARE_TEMPLATE_ID,
        "qqdocurl": clean_url,
        "showLittleTail": "",
        "shareOrigin": 0,
    }
    return {
        "app": "com.tencent.miniapp_01",
        "ver": "0.0.0.1",
        "appID": BILIBILI_MINIAPP_APPID,
        "view": BILIBILI_MINIAPP_VIEW,
        "prompt": "[QQ小程序]" + clean_title,
        "desc": "",
        "config": {
            "height": 0,
            "forward": 1,
            "ctime": ctime,
            "width": 0,
            "type": "normal",
            "token": "",
            "autoSize": 0,
        },
        "meta": {"detail_1": detail},
        "bthirdappforward": True,
        "bthirdappforwardforbackendswitch": True,
    }


def _deep_fill(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """把 extra 里「base 还空着」的字段补上（就地修改 base）。"""

    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_fill(base[key], value)
            continue
        if base.get(key) in ("", None, {}, []):
            base[key] = value
    return base


def fill_miniapp_payload(
    payload: Dict[str, Any], ark: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """用官方通道（get_mini_app_ark）返回的卡片补全本地模板。

    只补空白字段，绝不覆盖我们自己拼出来的 appID / view / config；
    官方给了合法的 view_<模板ID> 与 appID 时才让它们接管（那种情况说明
    对端给了真正可解析的小程序模板）。
    """

    if not isinstance(payload, dict) or not payload:
        return {}
    if not isinstance(ark, dict) or not ark:
        return payload
    merged = copy.deepcopy(payload)
    _deep_fill(merged, ark)
    view = str(ark.get("view") or "")
    app_id = str(ark.get("appID") or "").strip()
    if app_id and _VIEW_RE.match(view):
        merged["view"] = view
        merged["appID"] = app_id
    return merged

def build_news_payload(
    *,
    title: str,
    jump_url: str,
    cover: str = "",
    desc: str = "",
    tag: str = "哔哩哔哩",
    pub_time: int = 0,
) -> Dict[str, Any]:
    """拼 1 张通用图文卡片（structmsg/news，兼容性比小程序卡片好）。"""

    clean_title = clean_text(title, MAX_TITLE_CHARS)
    clean_url = (jump_url or "").strip()
    if not clean_title or not clean_url:
        return {}
    ctime = int(pub_time or 0)
    news = {
        "action": "",
        "appid": BILIBILI_APPID,
        "appid_type": "",
        "app_type": 0,
        "ctime": ctime,
        "desc": clean_text(desc, MAX_DESC_CHARS),
        "jumpUrl": clean_url,
        "preview": normalize_cover(cover),
        "tag": tag,
        "title": clean_title,
        "uin": 0,
    }
    return {
        "app": "com.tencent.structmsg",
        "desc": "新闻",
        "view": "news",
        "ver": "0.0.0.1",
        "prompt": "[分享]" + clean_title,
        "config": {"autosize": True, "ctime": ctime, "forward": True, "type": "normal"},
        "meta": {"news": news},
    }


def build_payload(
    kind: str,
    *,
    title: str,
    jump_url: str,
    cover: str = "",
    desc: str = "",
    tag: str = "哔哩哔哩",
    pub_time: int = 0,
    host_uin: Any = 0,
    host_nick: str = "",
) -> Dict[str, Any]:
    """按 kind 选卡片样式；首选样式拼不出来就换另一种。"""

    wanted = (kind or "miniapp").strip().lower()
    builders = (build_news_payload, build_miniapp_payload)
    if wanted != "news":
        builders = (build_miniapp_payload, build_news_payload)
    for builder in builders:
        kwargs: Dict[str, Any] = {
            "title": title,
            "jump_url": jump_url,
            "cover": cover,
            "desc": desc,
            "tag": tag,
            "pub_time": pub_time,
        }
        if builder is build_miniapp_payload:
            kwargs["host_uin"] = host_uin
            kwargs["host_nick"] = host_nick
        payload = builder(**kwargs)
        if payload:
            return payload
    return {}


def build_json_segment(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把卡片字典包成 OneBot 的 ``json`` 段。"""

    return {
        "type": "json",
        "data": {"data": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    }


def _api_url(api_base: str, action: str) -> str:
    """拼 OneBot HTTP 地址；ws:// 会自动当成 http://。"""

    base = (api_base or "").strip()
    if not base:
        return ""
    if base.startswith("ws://"):
        base = "http://" + base[len("ws://"):]
    elif base.startswith("wss://"):
        base = "https://" + base[len("wss://"):]
    elif not base.startswith(("http://", "https://")):
        base = "http://" + base
    base = base.rstrip("/")
    if base.endswith("/" + action):
        return base
    return base + "/" + action


def response_ok(data: Any) -> bool:
    """宽松判断 OneBot 接口是否成功（NapCat 返回 retcode，旧版返回 code）。"""

    if not isinstance(data, dict):
        return False
    status = str(data.get("status") or "").strip().lower()
    if status in ("failed", "error", "fail"):
        return False
    if "retcode" in data and data.get("retcode") not in (0, None):
        return False
    if "code" in data:
        code = data.get("code")
        if code not in (0, 200, None, "0", "200"):
            return False
        return True
    if "retcode" in data:
        return True
    if status == "ok":
        return True
    return "data" in data


def response_detail(data: Any) -> str:
    """从接口返回里抽一句人能看懂的结果。"""

    if not isinstance(data, dict):
        return str(data)[:200]
    parts = []
    for key in ("message", "msg", "wording"):
        value = data.get(key)
        if value:
            parts.append(str(value))
    retcode = data.get("retcode")
    if retcode not in (0, None):
        parts.insert(0, "retcode=" + str(retcode))
    inner = data.get("data")
    if isinstance(inner, dict):
        message_id = inner.get("message_id")
        if message_id:
            parts.append("message_id=" + str(message_id))
    return "；".join(parts)[:300] or "ok"


def error_text(exc: BaseException) -> str:
    """把异常翻成一句能看懂的话（``httpx`` 的连接错误常常 ``str()`` 是空串）。"""

    if isinstance(exc, httpx.HTTPStatusError):
        return "HTTP " + str(exc.response.status_code)
    text = str(exc).strip()
    return text or type(exc).__name__


async def _post(
    url: str,
    body: Dict[str, Any],
    token: str,
    timeout: float,
    *,
    use_header: bool,
) -> Any:
    headers = {"Content-Type": "application/json"}
    params: Dict[str, Any] = {}
    if token and use_header:
        headers["Authorization"] = "Bearer " + token
    if token and not use_header:
        params["access_token"] = token
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=body, headers=headers, params=params)
        if response.status_code in (401, 403) and token and use_header:
            return await _post(url, body, token, timeout, use_header=False)
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                "HTTP " + str(response.status_code),
                request=response.request,
                response=response,
            )
        try:
            return response.json()
        except ValueError:
            return {"status": "ok", "raw": response.text[:200]}


def _coerce_id(value: Any) -> Any:
    """OneBot 的动作参数一般是整数；能转就转，不能转就原样留着。"""

    text = str(value or "").strip()
    if not text:
        return ""
    return int(text) if text.isdigit() else text


def _looks_unsupported(data: Any) -> bool:
    """接口表示「不认识这个动作」时返回 True（SnowLuma 只认通用 send_msg）。"""

    if not isinstance(data, dict):
        return False
    retcode = data.get("retcode")
    if isinstance(retcode, int) and not isinstance(retcode, bool) and retcode in (1404, 404):
        return True
    text = " ".join(
        str(data.get(key, "")) for key in ("message", "msg", "wording", "status", "error")
    ).lower()
    if not text:
        return False
    if "unknown action" in text or "unsupported" in text or "不支持" in text:
        return True
    return "action" in text and ("未知" in text or "unknown" in text)


async def send_json_card_ex(
    api_base: str,
    access_token: str = "",
    *,
    group_id: str = "",
    user_id: str = "",
    payload: Dict[str, Any],
    timeout: float = 8.0,
) -> Tuple[bool, str, str]:
    """把卡片直发给 QQ 客户端；返回 ``(是否成功, 说明, message_id)``。"""

    base = (api_base or "").strip()
    if not base:
        return False, "没有配置 OneBot HTTP 地址", ""
    if not payload:
        return False, "卡片内容是空的", ""
    target = _coerce_id(group_id)
    user = _coerce_id(user_id)
    if not target and not user:
        return False, "没有可用的群号或 QQ 号", ""
    segment = build_json_segment(payload)
    if target:
        attempts = [
            ("send_group_msg", {"group_id": target, "message": [segment]}),
            ("send_msg", {"message_type": "group", "group_id": target, "message": [segment]}),
        ]
    else:
        attempts = [
            ("send_private_msg", {"user_id": user, "message": [segment]}),
            ("send_msg", {"message_type": "private", "user_id": user, "message": [segment]}),
        ]

    detail = ""
    for index, (action, body) in enumerate(attempts):
        url = _api_url(base, action)
        try:
            data = await _post(url, body, access_token or "", timeout, use_header=True)
        except Exception as exc:  # noqa: BLE001 - 网络问题一律降级，不打断分享流程
            detail = error_text(exc)
            logger.warning("小程序卡片直发失败（%s）：%s", url, detail)
            if index + 1 < len(attempts):
                continue
            return False, "请求失败：" + detail[:200], ""
        if response_ok(data):
            detail = response_detail(data)
            logger.info("小程序卡片已发送（%s）：%s", action, detail)
            return True, detail, _message_id(data)
        detail = response_detail(data)
        if index + 1 < len(attempts) and _looks_unsupported(data):
            logger.info("接口不认识 %s，改用 send_msg 再试：%s", action, detail)
            continue
        logger.warning("小程序卡片直发被拒绝（%s）：%s", url, detail)
        return False, detail, ""
    return False, detail, ""


def _message_id(data: Any) -> str:
    """从 send_*_msg 的返回里取出 message_id（回查要用）。"""

    if not isinstance(data, dict):
        return ""
    inner = data.get("data")
    if isinstance(inner, dict):
        for key in ("message_id", "msg_id"):
            value = inner.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _flatten_text(value: Any, depth: int = 0) -> str:
    """把接口返回里所有字符串拼成一坨，方便找关键词。"""

    if depth > 6:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_text(item, depth + 1) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(item, depth + 1) for item in value)
    return ""


def looks_degraded(data: Any) -> bool:
    """判断一条消息是不是「卡片被 QQ 丢掉后的兜底文本」。"""

    text = _flatten_text(data)
    if not text:
        return False
    return any(marker in text for marker in DEGRADED_MARKERS)


async def send_json_card(
    api_base: str,
    access_token: str = "",
    *,
    group_id: str = "",
    user_id: str = "",
    payload: Dict[str, Any],
    timeout: float = 8.0,
) -> Tuple[bool, str]:
    """把卡片直发给 QQ 客户端；返回 ``(是否成功, 说明)``。"""

    ok, detail, _ = await send_json_card_ex(
        api_base,
        access_token,
        group_id=group_id,
        user_id=user_id,
        payload=payload,
        timeout=timeout,
    )
    return ok, detail


async def fetch_mini_app_ark(
    api_base: str,
    access_token: str = "",
    *,
    title: str,
    jump_url: str,
    cover: str = "",
    desc: str = "",
    timeout: float = 8.0,
) -> Dict[str, Any]:
    """让 SnowLuma 用官方 oidb 通道现生成一张 B 站小程序卡片。

    只有 SnowLuma 提供 ``get_mini_app_ark``（NapCat 没有），失败一律返回
    ``{}``，由调用方退回本地拼的 payload。
    """

    base = (api_base or "").strip()
    clean_title = clean_text(title, MAX_TITLE_CHARS)
    clean_url = (jump_url or "").strip()
    if not base or not clean_title or not clean_url:
        return {}
    body: Dict[str, Any] = {"type": "bili", "title": clean_title, "jumpUrl": clean_url}
    clean_desc = clean_text(desc, MAX_DESC_CHARS)
    if clean_desc:
        body["desc"] = clean_desc
    clean_cover = normalize_cover(cover)
    if clean_cover:
        body["picUrl"] = clean_cover
    url = _api_url(base, MINIAPP_ARK_ACTION)
    try:
        data = await _post(url, body, access_token or "", timeout, use_header=True)
    except Exception as exc:  # noqa: BLE001 - 拿不到就走本地 payload
        logger.info("生成小程序卡片失败（%s）：%s", url, error_text(exc))
        return {}
    if not response_ok(data):
        logger.info("生成小程序卡片被拒绝（%s）：%s", url, response_detail(data))
        return {}
    inner = data.get("data")
    card = inner.get("data") if isinstance(inner, dict) else None
    if not isinstance(card, dict) or not card.get("app") or not card.get("view"):
        logger.info("生成小程序卡片的返回不像卡片：%s", str(inner)[:200])
        return {}
    logger.info("已用 SnowLuma 官方通道生成卡片：%s", clean_title)
    return card


async def send_tuwen_ark(
    api_base: str,
    access_token: str = "",
    *,
    group_id: str = "",
    user_id: str = "",
    title: str,
    desc: str = "",
    jump_url: str,
    preview_url: str = "",
    summary: str = "[分享]",
    timeout: float = 8.0,
) -> Tuple[bool, str, str]:
    """让 QQ 客户端发一张原生「图文 Ark」卡片；返回 ``(是否成功, 说明, message_id)``。

    和小程序卡片不同，图文 Ark 不依赖任何小程序，QQ 各版本都能渲染；
    SnowLuma 有 ``send_tuwen_ark``，NapCat 没有（会自动失败，由调用方退回图文）。
    """

    base = (api_base or "").strip()
    if not base:
        return False, "没有配置 OneBot HTTP 地址", ""
    clean_title = clean_text(title, MAX_TITLE_CHARS)
    clean_url = (jump_url or "").strip()
    if not clean_title or not clean_url:
        return False, "图文 Ark 缺标题或链接", ""
    target = _coerce_id(group_id)
    user = _coerce_id(user_id)
    if not target and not user:
        return False, "没有可用的群号或 QQ 号", ""
    body: Dict[str, Any] = {
        "title": clean_title,
        "desc": clean_text(desc, MAX_DESC_CHARS) or clean_title,
        "jump_url": clean_url,
        "summary": summary or "[分享]",
    }
    clean_cover = normalize_cover(preview_url)
    if clean_cover:
        body["preview_url"] = clean_cover
    if target:
        body["group_id"] = target
    else:
        body["user_id"] = user
    url = _api_url(base, TUWEN_ARK_ACTION)
    try:
        data = await _post(url, body, access_token or "", timeout, use_header=True)
    except Exception as exc:  # noqa: BLE001 - 发不出去交给调用方退回图文
        detail = error_text(exc)
        logger.warning("图文 Ark 直发失败（%s）：%s", url, detail)
        return False, "请求失败：" + detail[:200], ""
    if response_ok(data):
        detail = response_detail(data)
        logger.info("图文 Ark 已发送（%s）：%s", TUWEN_ARK_ACTION, detail)
        return True, detail, _message_id(data)
    detail = response_detail(data)
    logger.warning("图文 Ark 被拒绝（%s）：%s", url, detail)
    return False, detail, ""


async def check_card_message(
    api_base: str,
    access_token: str = "",
    message_id: str = "",
    timeout: float = 5.0,
) -> Optional[bool]:
    """发出后回查这条消息：True=还是卡片，False=被降级成文本，None=查不到。"""

    base = (api_base or "").strip()
    mid = str(message_id or "").strip()
    if not base or not mid:
        return None
    url = _api_url(base, "get_msg")
    try:
        data = await _post(
            url, {"message_id": _coerce_id(mid)}, access_token or "", timeout, use_header=True
        )
    except Exception as exc:  # noqa: BLE001 - 回查失败不影响主流程
        logger.debug("回查消息失败（%s）：%s", url, error_text(exc))
        return None
    if not response_ok(data):
        return None
    inner = data.get("data")
    if not isinstance(inner, dict):
        return None
    return not looks_degraded(inner)


async def recall_message(
    api_base: str,
    access_token: str = "",
    message_id: str = "",
    timeout: float = 5.0,
) -> bool:
    """撤回一条消息（被降级的卡片要清掉，免得群里留条乱码）。"""

    base = (api_base or "").strip()
    mid = str(message_id or "").strip()
    if not base or not mid:
        return False
    url = _api_url(base, "delete_msg")
    try:
        data = await _post(
            url, {"message_id": _coerce_id(mid)}, access_token or "", timeout, use_header=True
        )
    except Exception as exc:  # noqa: BLE001 - 撤回失败只记日志
        logger.info("撤回被降级的卡片失败（%s）：%s", url, error_text(exc))
        return False
    ok = response_ok(data)
    logger.info("撤回被降级的卡片：%s", "成功" if ok else response_detail(data))
    return ok