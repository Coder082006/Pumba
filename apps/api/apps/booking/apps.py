from django.apps import AppConfig


class BookingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.booking"
    label = "booking"
    verbose_name = "Booking"

    def ready(self) -> None:
        """§8.9's subscribers, registered once the app registry is populated.

        Imported here rather than at module scope because `handlers` reaches
        `inventory` and `trip`, and importing either before the registry is
        ready is how a module ends up half-initialised.
        """
        from apps.booking import handlers

        handlers.register()
