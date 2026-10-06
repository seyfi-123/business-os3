import os, logging, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.notifications.channels.base import BaseChannel, DeliveryResult

logger = logging.getLogger(__name__)


class EmailChannel(BaseChannel):
    name = "email"
    def __init__(self):
        self.host = os.getenv("SMTP_HOST", "").strip()
        self.port = int(os.getenv("SMTP_PORT", "587") or 587)
        self.user = os.getenv("SMTP_USER", "").strip()
        self.password = os.getenv("SMTP_PASSWORD", "").strip()
        self.sender = os.getenv("SMTP_FROM", self.user).strip()
    def is_available(self): return bool(self.host and self.sender)
    def resolve_target(self, user): return getattr(user, "email", None) if user else None
    def send(self, notification, user, target):
        if not self.is_available():
            return DeliveryResult("SKIPPED", error="email not configured")
        if not target:
            return DeliveryResult("SKIPPED", error="no email")
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = notification.title_tj
            msg["From"] = self.sender
            msg["To"] = target
            msg.attach(MIMEText(notification.body_tj, "plain", "utf-8"))
            with smtplib.SMTP(self.host, self.port, timeout=10) as s:
                s.starttls()
                if self.user:
                    s.login(self.user, self.password)
                s.sendmail(self.sender, [target], msg.as_string())
            return DeliveryResult("SENT", target=target)
        except Exception as e:
            logger.exception("Email failed")
            return DeliveryResult("FAILED", target=target, error=str(e)[:200])
