from app.notifications.channels.in_app import InAppChannel
from app.notifications.channels.telegram import TelegramChannel
from app.notifications.channels.email import EmailChannel

CHANNELS = {"in_app": InAppChannel(), "telegram": TelegramChannel(),
            "email": EmailChannel()}
