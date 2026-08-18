"""Column types the SRS names that Django does not ship.

SRS §7.5.1 specifies `CITEXT` for `user.email` and §7.5.3 for
`provider.contact_email`. Django's `django.contrib.postgres.fields.CIText*`
classes were deprecated in 4.2 and removed in 5.1, with the documented
replacement being a case-insensitive collation — which is per-column,
non-portable to declare, and does not survive a `makemigrations` round trip
as legibly as a type does.

So the type is declared directly. `citext` is a first-class PostgreSQL type,
it is what the SRS asks for, and it puts case-insensitivity in the schema
rather than in every query that touches an address.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import models

__all__ = ["CITextField"]

if TYPE_CHECKING:
    # django-stubs makes model fields generic in (set type, get type); the
    # runtime classes are not subscriptable, so the parameters are applied
    # only where the type checker can see them.
    _TextFieldBase = models.TextField[str, str]
else:
    _TextFieldBase = models.TextField


class CITextField(_TextFieldBase):
    """A case-insensitive text column.

    Requires the `citext` extension, which the identity migration installs.
    """

    def db_type(self, connection: Any) -> str:
        return "citext"
