from django.apps import AppConfig


class AdministrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.administration"
    label = "administration"
    verbose_name = "Administration"

    def ready(self) -> None:
        """Install the audit sink, and subscribe §19.1's payment emails.

        Both are registered here rather than at import time because they need
        the model registry to be populated. Until the sink runs, `record_audit`
        still writes to the application log — the port degrades, it does not
        fail — and until the handlers register, a confirmed trip simply sends
        no mail.
        """
        from apps.administration import handlers
        from apps.administration.services import write_audit_record
        from apps.common.audit import register_sink

        register_sink(write_audit_record)
        handlers.register()
