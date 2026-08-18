from django.apps import AppConfig


class AdministrationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.administration"
    label = "administration"
    verbose_name = "Administration"

    def ready(self) -> None:
        """Install the database-backed audit sink.

        Registered here rather than at import time because it needs the model
        registry to be populated. Until this runs, `record_audit` still writes
        to the application log — the port degrades, it does not fail.
        """
        from apps.administration.services import write_audit_record
        from apps.common.audit import register_sink

        register_sink(write_audit_record)
