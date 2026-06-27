"""飞书 SDK 客户端 — Lark Open API 封装。

支持两种发送模式：
- webhook：简单文本推送，无需 app 凭证
- API：消息卡片、富文本、交互式卡片，需要 app_id + app_secret
"""

import json
import time
from typing import Optional

import httpx
from loguru import logger


class FeishuClient:
    """飞书 Lark Open API 客户端。

    tenant_access_token 自动获取和刷新，有效期 2 小时，提前 5 分钟刷新。
    """

    BASE_URL = "https://open.feishu.cn/open-apis"
    TOKEN_EXPIRE_BUFFER = 300  # 提前 5 分钟刷新

    def __init__(
        self,
        webhook_url: str = "",
        app_id: str = "",
        app_secret: str = "",
    ):
        self.webhook_url = webhook_url
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: Optional[str] = None
        self._token_expire_at: float = 0
        self._http: Optional[httpx.AsyncClient] = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    # ── token 管理 ──────────────────────────────────────────

    async def _ensure_token(self) -> str:
        """获取或刷新 tenant_access_token。"""
        now = time.monotonic()
        if self._token and now + self.TOKEN_EXPIRE_BUFFER < self._token_expire_at:
            return self._token

        if not self.app_id or not self.app_secret:
            raise RuntimeError("飞书 app_id / app_secret 未配置，无法获取 tenant_access_token")

        http = await self._get_http()
        resp = await http.post(
            f"{self.BASE_URL}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取 tenant_access_token 失败: {data}")

        self._token = data["tenant_access_token"]
        self._token_expire_at = now + data.get("expire", 7200)
        logger.info("[飞书] tenant_access_token 已刷新")
        return self._token

    # ── 消息发送 ────────────────────────────────────────────

    async def send_webhook(self, text: str) -> bool:
        """通过 webhook 发送文本消息（无需 app 凭证）。"""
        if not self.webhook_url:
            logger.warning("[飞书] webhook_url 未配置，跳过发送")
            return False

        http = await self._get_http()
        try:
            resp = await http.post(
                self.webhook_url,
                json={"msg_type": "text", "content": {"text": text}},
            )
            logger.info(f"[飞书 Webhook] 发送成功: {text[:100]}")
            return True
        except Exception as e:
            logger.error(f"[飞书 Webhook] 发送失败: {e}")
            return False

    async def send_text(self, receive_id: str, text: str) -> bool:
        """通过 API 向指定用户/群发送文本消息（需要 app 凭证）。"""
        return await self._send_message(receive_id, "text", json.dumps({"text": text}))

    async def send_card(self, receive_id: str, card: dict) -> bool:
        """通过 API 发送交互式卡片消息。"""
        return await self._send_message(receive_id, "interactive", json.dumps(card))

    async def _send_message(self, receive_id: str, msg_type: str, content: str) -> bool:
        token = await self._ensure_token()
        http = await self._get_http()
        payload = {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
        }
        resp = await http.post(
            f"{self.BASE_URL}/im/v1/messages?receive_id_type=open_id",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.error(f"[飞书] 发送消息失败: {data}")
            return False
        logger.info(f"[飞书] 消息已发送: msg_type={msg_type} msg_id={data.get('data',{}).get('message_id','?')}")
        return True

    async def reply_text(self, message_id: str, text: str) -> bool:
        """回复指定消息（文本）。"""
        token = await self._ensure_token()
        http = await self._get_http()
        payload = {
            "content": json.dumps({"text": text}),
            "msg_type": "text",
        }
        resp = await http.post(
            f"{self.BASE_URL}/im/v1/messages/{message_id}/reply",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.error(f"[飞书] 回复消息失败: {data}")
            return False
        return True

    async def send_alert(self, receive_id: str, level: str, title: str, body: str) -> bool:
        """发送告警消息（webhook 优先，回退 API）。"""
        tag = {"info": "ℹ️", "warn": "⚠️", "error": "🚨"}.get(level, "🔔")
        text = f"{tag} [{level.upper()}] {title}\n\n{body}"
        if self.webhook_url:
            return await self.send_webhook(text)
        return await self.send_text(receive_id, text)

    async def close(self):
        if self._http:
            await self._http.aclose()
            self._http = None
