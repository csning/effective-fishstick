"""飞书 Bot 服务启动器 — uvicorn 进程管理。"""

import asyncio
import signal
import sys

import uvicorn
from loguru import logger

from config import get_settings


class Server:
    """飞书 Bot Web 服务封装。"""

    def __init__(self, host: str = "0.0.0.0", port: int = 8000):
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None

    async def start(self):
        settings = get_settings()
        await self._print_diagnostics(settings)

        config = uvicorn.Config(
            "web.app:app",
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        await self._server.serve()

    async def _print_diagnostics(self, settings):
        """启动时打印配置状态和关键信息。"""
        fw = "-" * 50
        logger.info(fw)
        logger.info("  Effective Fishstick v0.1.0 - Feishu Bot")
        logger.info(f"  {self.host}:{self.port}")
        logger.info(fw)

        app_ok = bool(settings.notify.feishu_app_id)
        secret_ok = bool(settings.notify.feishu_app_secret)
        webhook_ok = bool(settings.notify.feishu_webhook)

        msg = "Config: "
        msg += f"app_id={'OK' if app_ok else 'MISSING'} "
        msg += f"app_secret={'OK' if secret_ok else 'MISSING'} "
        msg += f"webhook={'OK' if webhook_ok else 'unset'}"
        logger.info(msg)

        if app_ok and secret_ok:
            try:
                from notify.feishu import FeishuClient
                c = FeishuClient(app_id=settings.notify.feishu_app_id, app_secret=settings.notify.feishu_app_secret)
                await c._ensure_token()
                logger.info("Feishu API: connected OK")
                await c.close()
            except Exception as e:
                logger.warning(f"Feishu API: connect failed ({e})")

        logger.info(f"Endpoint POST /feishu/webhook (event subscription)")
        logger.info(f"Diag    GET  /feishu/health  (config status)")
        logger.info(f"Health  GET  /health          (liveness)")
        logger.info(fw)

    def _shutdown(self):
        if self._server:
            logger.info("Received shutdown signal, stopping...")
            self._server.should_exit = True
