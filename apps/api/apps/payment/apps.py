from django.apps import AppConfig


class PaymentConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payment"
    label = "payment"
    verbose_name = "Payment"

    def ready(self) -> None:
        """§8.9's subscribers, registered once the app registry is populated.

        Imported here rather than at module scope because `handlers` reaches
        `booking`, and importing another app before the registry is ready is
        how a module ends up half-initialised.
        """
        from apps.payment import handlers

        handlers.register()
