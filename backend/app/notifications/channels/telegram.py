import os, logging, urllib.request, urllib.parse
from app.notifications.channels.base import BaseChannel, DeliveryResult

logger = logging.getLogger(__name__)


class TelegramChannel(BaseChannel):
    name = "telegram"
    def __init__(self):
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    def is_available(self): return bool(self.token)
    def resolve_target(self, user): return None
    def send(self, notification, user, target):
        if not self.is_available():
            return DeliveryResult("SKIPPED", error="telegram not configured")
        if not target:
            return DeliveryResult("SKIPPED", error="no chat_id")
        try:
            text = f"*{notification.title_tj}*\n\n{notification.body_tj}"
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": target, "text": text,
                                            "parse_mode": "Markdown"}).encode()
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return DeliveryResult("SENT", target=target)
                return DeliveryResult("FAILED", target=target,
                                       error=f"HTTP {resp.status}")
        except Exception as e:
            logger.exception("Telegram failed")
            return DeliveryResult("FAILED", target=target, error=str(e)[:200])
