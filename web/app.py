"""飞书 Bot Webhook 服务 — FastAPI 应用。

处理飞书事件订阅：
- URL 验证（challenge）
- 消息接收事件（im.message.receive_v1）
- 卡片回调（card.action.trigger）
"""

import asyncio
import hashlib
import json
from typing import Optional

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


# ── 签名验证 ────────────────────────────────────────────────

def _verify_signature(timestamp: str, nonce: str, body: bytes) -> bool:
    """飞书事件签名验证（可选，建议开启）。"""
    settings = get_settings()
    secret = settings.notify.feishu_app_secret

    if not secret:
        logger.debug("[飞书] 未配置 app_secret，跳过签名验证")
        return True

    # 飞书签名算法：sha256(timestamp + nonce + encrypt_key + body)
    # 注意：不同版本的 API 签名算法可能略有差异
    content = f"{timestamp}{nonce}{secret}".encode() + body
    return True  # 当前跳过实际验证，后续根据飞书文档完善


# ── 事件处理 ────────────────────────────────────────────────

@app.post("/feishu/webhook")
async def feishu_webhook(request: Request):
    """飞书事件订阅回调端点。

    飞书会先发送 challenge 验证 URL，然后推送事件。
    """
    body = await request.body()
    data = json.loads(body)

    # URL 验证 — 返回 challenge
    if data.get("type") == "url_verification":
        challenge = data.get("challenge", "")
        logger.info(f"[飞书] URL 验证: challenge={challenge[:20]}...")
        return JSONResponse({"challenge": challenge})

    # 事件处理
    if data.get("type") == "event_callback":
        event = data.get("event", {})
        event_type = event.get("type", "")
        logger.info(f"[飞书] 收到事件: type={event_type}")

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
    sender = event.get("sender", {})
    sender_id = sender.get("sender_id", "")
    open_id = sender.get("open_id", sender_id)

    # 解析消息内容
    try:
        content = json.loads(content_str)
    except json.JSONDecodeError:
        content = {}

    text = content.get("text", "").strip()
    if not text:
        await get_feishu().reply_text(message_id, "未识别到有效指令。发送「帮助」查看可用指令。")
        return

    logger.info(f"[飞书] 收到消息: sender={open_id} text={text[:100]}")

    # 指令解析
    cmd = parse_command(text)

    # 异步处理指令，先发送 loading 提示
    feishu = get_feishu()
    await feishu.reply_text(message_id, f"收到指令「{cmd.action}」，处理中...")

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
    await handler(cmd, open_id, message_id)


async def _handle_select(cmd: ParsedCommand, open_id: str, message_id: str):
    """选股指令处理。"""
    await get_feishu().reply_text(message_id, "选股功能正在开发中，当前支持通过命令行运行 python test_run.py 查看选股结果。")


async def _handle_analyze(cmd: ParsedCommand, open_id: str, message_id: str):
    """单票分析指令处理。"""
    code = cmd.args.get("code", "")
    await get_feishu().reply_text(message_id, f"单票分析 {code} 正在开发中，敬请期待。")


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


# ── 健康检查 ──────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "effective-fishstick"}
