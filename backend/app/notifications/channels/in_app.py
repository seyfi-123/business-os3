from app.notifications.channels.base import BaseChannel, DeliveryResult


class InAppChannel(BaseChannel):
    name = "in_app"
    def is_available(self): return True
    def resolve_target(self, user): return f"user:{user.id}" if user else None
    def send(self, notification, user, target):
        return DeliveryResult("SENT", target or "in_app")
