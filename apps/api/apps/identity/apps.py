from django.apps import AppConfig


class IdentityConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.identity"
    label = "identity"
    verbose_name = "Identity"

    def ready(self) -> None:
        """Install the principal loader the shared kernel authenticates with.

        `apps.common` is a leaf and cannot import this module, so the
        authentication class reaches `get_principal()` through a registry —
        the same arrangement as the settings read port and the audit write
        port. Registered here because it needs the model registry populated.
        """
        from apps.common.authz.loader import register_principal_loader
        from apps.identity.services import get_principal

        register_principal_loader(get_principal)
