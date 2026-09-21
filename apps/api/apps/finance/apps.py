from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.finance"
    label = "finance"
    verbose_name = "Finance"

    def ready(self) -> None:
        """§22.2's resolver, and §22.3's accrual, wired once per process.

        Registered here rather than at import time because both reach the model
        registry. Until this runs, `common.commission` answers with the
        platform default and no accrual is recorded — a degradation that is
        visible in a booking's snapshot rather than silent.
        """
        from apps.common.commission import register_resolver
        from apps.finance import handlers
        from apps.finance.services import resolve_commission

        register_resolver(resolve_commission)
        handlers.register()
