import os


class BillingSettings:
    PAYMENT_MODE: str = os.getenv("BILLING_PAYMENT_MODE", "DEMO").upper()
    PROVIDER_WEBHOOK_SECRET: str = os.getenv("BILLING_WEBHOOK_SECRET", "")
    @property
    def demo_enabled(self):
        return self.PAYMENT_MODE == "DEMO"


billing_settings = BillingSettings()
