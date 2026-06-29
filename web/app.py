"""飞书 Bot Webhook 服务 — FastAPI 应用。

处理飞书事件订阅：
- URL 验证（challenge）
- 消息接收事件（im.message.receive_v1）
- 卡片回调（card.action.trigger）
"""

import asyncio
import hashlib
import json
import time
import traceback
from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from config import get_settings
from notify.feishu import FeishuClient
from notify.commands import parse_command, ParsedCommand
from notify.cards import (
    build_stock_selection_card,
    build_anomaly_alert_card,
    build_position_card,
    build_review_card,
    build_help_card,
    build_loading_card,
    build_error_card,
)

app = FastAPI(title="Effective Fishstick - Feishu Bot", version="0.1.0")

# ── 全局状态 ────────────────────────────────────────────────

_feishu: Optional[FeishuClient] = None
_command_tasks: dict[str, asyncio.Task] = {}

# 最近事件记录（用于诊断）
_recent_events: list[dict] = []
_max_recent_events = 20
TZ = timezone(timedelta(hours=8))
_startup_time = datetime.now(TZ)


def _record_event(event_type: str, detail: dict):
    _recent_events.append({
        "time": datetime.now(TZ).isoformat(),
        "type": event_type,
        "detail": detail,
    })
    if len(_recent_events) > _max_recent_events:
        _recent_events.pop(0)


def get_feishu() -> FeishuClient:
    global _feishu
    if _feishu is None:
        settings = get_settings()
        _feishu = FeishuClient(
            webhook_url=settings.notify.feishu_webhook,
            app_id=settings.notify.feishu_app_id,
            app_secret=settings.notify.feishu_app_secret,
        )
    return _feishu


# ── 辅助函数 ────────────────────────────────────────────────

def _extract_open_id(event: dict) -> str:
    """从飞书事件中提取发送者的 open_id。

    飞书 v2 事件格式中 sender.sender_id 是一个对象：
    {"open_id": "...", "union_id": "...", "user_id": "..."}
    """
    sender = event.get("sender", {})
    sender_id = sender.get("sender_id", "")

    # 飞书 v2：sender_id 是对象
    if isinstance(sender_id, dict):
        open_id = sender_id.get("open_id", "")
        if open_id:
            return open_id
        return sender_id.get("union_id", sender_id.get("user_id", ""))

    # 飞书 v1 / 旧格式：sender_id 是字符串
    if isinstance(sender_id, str) and sender_id:
        return sender_id

    # 兜底
    return sender.get("open_id", "")


def _verify_signature(timestamp: str, nonce: str, body: bytes) -> bool:
    """飞书事件签名验证（当前跳过，后续按飞书文档完善）。"""
    settings = get_settings()
    secret = settings.notify.feishu_app_secret

    if not secret:
        logger.debug("[飞书] 未配置 app_secret，跳过签名验证")
        return True

    return True  # 当前跳过实际验证，后续根据飞书文档完善


# ── 事件处理 ────────────────────────────────────────────────

@app.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """飞书事件订阅回调端点。

    飞书会先发送 challenge 验证 URL，然后推送事件。
    """
    body = await request.body()
    headers = dict(request.headers)
    client_ip = request.client.host if request.client else "unknown"

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error(f"[飞书] JSON 解析失败: {e} | body={body[:500]}")
        _record_event("json_error", {"error": str(e)})
        return JSONResponse({"code": -1, "msg": "invalid json"}, status_code=400)

    # URL 验证 — 返回 challenge
    if data.get("type") == "url_verification":
        challenge = data.get("challenge", "")
        logger.info(f"[飞书] URL 验证 成功 | challenge={challenge[:20]}... | ip={client_ip}")
        _record_event("url_verification", {"challenge": challenge[:20], "ip": client_ip})
        return JSONResponse({"challenge": challenge})

    # 事件处理 — 兼容 v1 (type: event_callback) 和 v2 (schema: 2.0)
    event_type = ""
    event = {}
    schema_version = data.get("schema", "?")

    if schema_version == "2.0":
        # 飞书 v2：event_type 在 header.event_type
        header = data.get("header", {})
        event_type = header.get("event_type", "")
        event = data.get("event", {})
    elif data.get("type") == "event_callback":
        # 飞书 v1：event_type 在 event.type
        event = data.get("event", {})
        event_type = event.get("type", "")
    else:
        # 未知格式 — 记录并忽略
        logger.warning(f"[飞书] 未知事件格式, keys={list(data.keys())[:8]} ip={client_ip}")
        _record_event("unknown_format", {"keys": list(data.keys())[:8]})

    if event_type:
        logger.info(f"[飞书] 收到事件 type={event_type} schema={schema_version} ip={client_ip}")
        logger.debug(f"[飞书] 事件详情: {json.dumps(event, ensure_ascii=False)[:800]}")
        _record_event(event_type, {"schema": schema_version})

        if event_type == "im.message.receive_v1":
            asyncio.create_task(_handle_message(event))
        elif event_type == "card.action.trigger":
            asyncio.create_task(_handle_card_action(event))

    return JSONResponse({"code": 0})


async def _handle_message(event: dict):
    """处理用户消息事件。"""
    message = event.get("message", {})
    message_id = message.get("message_id", "")
    content_str = message.get("content", "{}")
    open_id = _extract_open_id(event)

    # 解析消息内容
    try:
        content = json.loads(content_str)
    except json.JSONDecodeError:
        content = {}

    text = content.get("text", "").strip()
    if not text:
        try:
            await get_feishu().reply_text(message_id, "未识别到有效指令。发送「帮助」查看可用指令。")
        except Exception as e:
            logger.error(f"[飞书] 回复空指令失败: {e}")
        return

    logger.info(f"[飞书] 收到消息 open_id={open_id[:12]}... msg_id={message_id[:12]}... text={text[:100]}")

    # 指令解析
    cmd = parse_command(text)

    # 异步处理指令，先发送 loading 提示
    feishu = get_feishu()
    try:
        await feishu.reply_text(message_id, f"收到指令「{cmd.action}」，处理中...")
    except Exception as e:
        logger.error(f"[飞书] 发送 loading 回复失败: {e}")
        logger.error(traceback.format_exc())

    # 分发到对应处理器
    handler_map = {
        "select": _handle_select,
        "analyze": _handle_analyze,
        "position": _handle_position,
        "review": _handle_review,
        "risk": _handle_risk,
        "help": _handle_help,
        "unknown": _handle_unknown,
    }

    handler = handler_map.get(cmd.action, _handle_unknown)
    try:
        await handler(cmd, open_id, message_id)
    except Exception as e:
        logger.error(f"[飞书] 指令处理异常 cmd={cmd.action}: {e}")
        logger.error(traceback.format_exc())
        try:
            feishu = get_feishu()
            await feishu.reply_text(message_id, f"处理指令时出错: {e}")
        except Exception:
            pass


async def _handle_select(cmd: ParsedCommand, open_id: str, message_id: str):
    """选股指令处理。"""
    await get_feishu().reply_text(message_id, "选股功能正在开发中。\n当前可通过命令行运行选股：\n```\npython test_run.py\n```")


async def _handle_analyze(cmd: ParsedCommand, open_id: str, message_id: str):
    """单票分析指令处理。"""
    code = cmd.args.get("code", "")
    await get_feishu().reply_text(message_id, f"单票分析 {code} 正在开发中。\n当前可通过命令行运行：\n```\npython test_run.py\n```")


async def _handle_position(cmd: ParsedCommand, open_id: str, message_id: str):
    """持仓指令处理。"""
    await get_feishu().reply_text(message_id, "持仓诊断功能正在开发中，敬请期待。")


async def _handle_review(cmd: ParsedCommand, open_id: str, message_id: str):
    """复盘指令处理。"""
    await get_feishu().reply_text(message_id, "每日复盘功能正在开发中，敬请期待。")


async def _handle_risk(cmd: ParsedCommand, open_id: str, message_id: str):
    """风控指令处理。"""
    level = cmd.args.get("level")
    if level is not None:
        await get_feishu().reply_text(message_id, f"风险等级已锁定为 {level}/5（功能开发中）。")
    else:
        await get_feishu().reply_text(message_id, "当前风险等级 3/5 · 正常模式（功能开发中）。")


async def _handle_help(cmd: ParsedCommand, open_id: str, message_id: str):
    """帮助指令处理。"""
    card = build_help_card()
    await get_feishu().send_card(open_id, card)


async def _handle_unknown(cmd: ParsedCommand, open_id: str, message_id: str):
    """未知指令处理。"""
    await get_feishu().reply_text(
        message_id,
        f"未识别指令「{cmd.raw}」。\n发送「帮助」查看可用指令。"
    )


async def _handle_card_action(event: dict):
    """处理卡片按钮回调。"""
    action = event.get("action", {})
    value = action.get("value", {})
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}

    cmd_action = value.get("cmd", "")
    logger.info(f"[飞书] 卡片回调: cmd={cmd_action}")
    # 卡片回调暂时不处理，后续通过按钮触发二次操作


# ── 诊断端点 ──────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "effective-fishstick"}


@app.get("/feishu/health")
async def feishu_health():
    """飞书 Bot 诊断端点。检查配置状态和最近事件。"""
    settings = get_settings()
    uptime = datetime.now(TZ) - _startup_time
    return {
        "status": "ok",
        "service": "effective-fishstick",
        "uptime_seconds": int(uptime.total_seconds()),
        "feishu": {
            "app_id_configured": bool(settings.notify.feishu_app_id),
            "app_secret_configured": bool(settings.notify.feishu_app_secret),
            "webhook_configured": bool(settings.notify.feishu_webhook),
            "channel": settings.notify.channel,
        },
        "recent_events": _recent_events[-10:],
    }
