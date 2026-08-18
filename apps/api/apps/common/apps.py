from django.apps import AppConfig


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.common"
    label = "common"
    verbose_name = "Common"

    def ready(self) -> None:
        """Import the OpenAPI extensions so they register themselves.

        drf-spectacular's extension registry is populated as a side effect of
        subclassing, so the module has to be imported for the security scheme
        to exist. Without it every authenticated operation is documented as
        public — worse than a missing document, since §30.8 publishes this.
        """
        from apps.common import schema  # noqa: F401
