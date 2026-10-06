from dataclasses import dataclass


@dataclass
class DeliveryResult:
    status: str
    target: str | None = None
    error: str | None = None


class BaseChannel:
    name = "base"
    def is_available(self): return False
    def resolve_target(self, user): return None
    def send(self, notification, user, target):
        raise NotImplementedError
