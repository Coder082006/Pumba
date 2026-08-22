"""Serializer behaviour shared by every module — SRS §30.6.

    §30.6: "unknown fields rejected rather than ignored"; "mass assignment is
    prevented by explicit serializer field lists".

`StrictSerializer` began in `identity` and lives here because `catalogue`
needs the same rule and may not import `identity` (§6.4: `catalogue` depends
on `location` alone). That is the shared-kernel case ADR 0005 describes: a
control applied identically by every module belongs in `common`, not in
whichever module happened to need it first.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

__all__ = ["StrictSerializer"]


class StrictSerializer(serializers.Serializer[Any]):
    """Rejects unknown fields — SRS §30.6.

    DRF ignores them by default, which turns a client's typo into silence and
    lets a renamed field keep "working" while doing nothing.

    It is also half of the write path's mass-assignment defence. The other
    half is `apps.catalogue.repositories._WRITABLE`, and the duplication is
    deliberate: this one gives the administrator a 422 naming the field they
    got wrong, and that one holds even for a caller that never passed through
    a serializer — the seed loader, a management command, a console shell.
    """

    def to_internal_value(self, data: Any) -> Any:
        if isinstance(data, dict):
            unknown = set(data) - set(self.fields)
            if unknown:
                raise serializers.ValidationError(
                    {field: "Unrecognised field." for field in sorted(unknown)}
                )
        return super().to_internal_value(data)
