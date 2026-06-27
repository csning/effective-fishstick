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
        logger.info(f"启动飞书 Bot 服务 -> {self.host}:{self.port}")

        config = uvicorn.Config(
            "web.app:app",
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        # 注册优雅退出
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        await self._server.serve()

    def _shutdown(self):
        if self._server:
            logger.info("收到退出信号，关闭服务...")
            self._server.should_exit = True


