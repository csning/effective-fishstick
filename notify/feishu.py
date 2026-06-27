from loguru import logger


class FeishuNotifier:

    def __init__(self, webhook_url: str = "", app_id: str = "", app_secret: str = ""):
        self.webhook_url = webhook_url
        self.app_id = app_id
        self.app_secret = app_secret

    async def send_text(self, text: str) -> bool:
        # TODO: POST to webhook or bot API
        logger.info(f"[Feishu] {text[:200]}")
        return True

    async def send_card(self, title: str, content_md: str) -> bool:
        # TODO: Feishu message card with interactive elements
        logger.info(f"[Feishu Card] {title}")
        return True

    async def send_alert(self, level: str, title: str, body: str) -> bool:
        logger.info(f"[Feishu {level.upper()}] {title}: {body[:200]}")
        return True
