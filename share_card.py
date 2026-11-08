"""QQ 小程序卡片（OneBot ``json`` 段）的构造与直发。

麦麦主程序和它自带的 QQ 适配器（SnowLuma / NapCat）都会把 OneBot 的
``json`` 段降级成 ``[unsupported:json]`` 纯文本，所以想让分享真的以
小程序卡片的形式出现，只能由插件自己 POST 到 QQ 客户端的 OneBot HTTP
接口（NapCat 默认 3000 端口，SnowLuma 同理）。

这个模块刻意只依赖 httpx：不碰麦麦 SDK，方便单测与复用。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger("plugin.bilibili_interest_share.share_card")

# B 站小程序的 AppID（手机上把 B 站视频转发给好友时用的就是它）
BILIBILI_APPID = 1109937557
# 卡片左侧小图标；拿不到也不影响卡片显示
BILIBILI_ICON = "https://www.bilibili.com/favicon.ico"
# 标题/简介的长度上限（太长 QQ 会截断，先自己截干净）
MAX_TITLE_CHARS = 60
MAX_DESC_CHARS = 80


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


def _miniapp_detail(
    *,
    title: str,
    jump_url: str,
    cover: str,
    desc: str = "",
    tag: str = "哔哩哔哩",
    pub_time: int = 0,
    icon: str = BILIBILI_ICON,
) -> Dict[str, Any]:
    detail: Dict[str, Any] = {
        "title": title,
        "desc": desc or tag,
        "icon": icon,
        "preview": cover,
        "url": jump_url,
        "appid": BILIBILI_APPID,
        "scene": 1036,
        "hostType": 0,
        "shareTemplateId": "0",
        "qqdocurl": jump_url,
        "pubTime": int(pub_time or 0),
        "type": 0,
        "appType": 0,
        "uin": 0,
    }
    if tag:
        detail["tag"] = tag
    return detail


def build_miniapp_payload(
    *,
    title: str,
    jump_url: str,
    cover: str,
    desc: str = "",
    tag: str = "哔哩哔哩",
    pub_time: int = 0,
    icon: str = BILIBILI_ICON,
) -> Dict[str, Any]:
    """拼 1 张「B 站小程序」卡片（和手机里转发出来的一样）。

    缺标题、链接或封面时返回 ``{}``——调用方据此退回图文。
    """

    clean_title = clean_text(title, MAX_TITLE_CHARS)
    clean_url = (jump_url or "").strip()
    clean_cover = normalize_cover(cover)
    if not clean_title or not clean_url or not clean_cover:
        return {}
    ctime = int(pub_time or 0)
    return {
        "app": "com.tencent.miniapp_01",
        "desc": "0",
        "view": "viewMultiMsg",
        "ver": "0.0.0.1",
        "prompt": "[" + tag + "]" + clean_title,
        "config": {
            "ctime": ctime,
            "forward": True,
            "type": "normal",
            "autoSize": True,
            "height": 0,
            "width": 0,
        },
        "meta": {
            "detail_1": _miniapp_detail(
                title=clean_title,
                jump_url=clean_url,
                cover=clean_cover,
                desc=clean_text(desc, MAX_DESC_CHARS),
                tag=tag,
                pub_time=ctime,
                icon=icon,
            )
        },
        "extra": {"app_type": 0, "appid": BILIBILI_APPID, "uin": 0},
    }


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
) -> Dict[str, Any]:
    """按 ``kind`` 选卡片样式；首选样式拼不出来就换另一种。"""

    wanted = (kind or "miniapp").strip().lower()
    builders = (build_news_payload, build_miniapp_payload)
    if wanted != "news":
        builders = (build_miniapp_payload, build_news_payload)
    for builder in builders:
        payload = builder(
            title=title,
            jump_url=jump_url,
            cover=cover,
            desc=desc,
            tag=tag,
            pub_time=pub_time,
        )
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

    base = (api_base or "").strip()
    if not base:
        return False, "没有配置 OneBot HTTP 地址"
    if not payload:
        return False, "卡片内容是空的"
    target = _coerce_id(group_id)
    user = _coerce_id(user_id)
    if not target and not user:
        return False, "没有可用的群号或 QQ 号"
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
            return False, "请求失败：" + detail[:200]
        if response_ok(data):
            detail = response_detail(data)
            logger.info("小程序卡片已发送（%s）：%s", action, detail)
            return True, detail
        detail = response_detail(data)
        if index + 1 < len(attempts) and _looks_unsupported(data):
            logger.info("接口不认识 %s，改用 send_msg 再试：%s", action, detail)
            continue
        logger.warning("小程序卡片直发被拒绝（%s）：%s", url, detail)
        return False, detail
    return False, detail