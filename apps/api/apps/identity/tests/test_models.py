"""Structural tests for the identity schema — SRS §7.2, §7.5.1, §7.5.2.

No database. These assert the *declared* schema, which is where the §7.2
conventions either hold or quietly do not — a missing partial index or a
cascade pointing the wrong way is invisible in behaviour until the day it
matters.
"""

from __future__ import annotations

import importlib

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.db import models

from apps.common.authz import Role as RoleEnum
from apps.identity.models import (
    Device,
    OneTimeToken,
    Session,
    TouristProfile,
    User,
    UserRole,
    UserStatus,
)

#: The migration module is not an importable identifier.
SEED = importlib.import_module("apps.identity.migrations.0002_updated_at_triggers_and_roles")


def field(model: type[models.Model], name: str) -> models.Field:
    return model._meta.get_field(name)  # type: ignore[return-value]


def constraint_names(model: type[models.Model]) -> set[str]:
    return {c.name for c in model._meta.constraints}


class TestUserIsTheAuthModel:
    def test_auth_user_model_points_here(self) -> None:
        assert get_user_model() is User

    def test_django_permissions_are_not_inherited(self) -> None:
        """PermissionsMixin would be a second authorisation system alongside
        §5.2 and §30.3, and eventually the wrong one answers."""
        assert not hasattr(User, "user_permissions")
        assert not hasattr(User, "groups")
        assert not hasattr(User, "is_superuser")

    def test_the_username_field_is_email(self) -> None:
        assert User.USERNAME_FIELD == "email"


class TestUserMatchesSection751:
    def test_the_table_is_named_user(self) -> None:
        assert User._meta.db_table == "user"

    def test_the_password_column_is_password_hash(self) -> None:
        assert field(User, "password").db_column == "password_hash"

    def test_the_last_login_column_is_last_login_at(self) -> None:
        assert field(User, "last_login").db_column == "last_login_at"

    def test_email_is_citext(self) -> None:
        """§7.5.1: "CITEXT … Unique, case-insensitive"."""
        assert type(field(User, "email")).__name__ == "CITextField"

    def test_the_status_domain_matches_the_srs(self) -> None:
        assert {c.value for c in UserStatus} == {"PENDING", "ACTIVE", "SUSPENDED", "CLOSED"}

    def test_status_defaults_to_pending(self) -> None:
        assert field(User, "status").default == UserStatus.PENDING

    def test_the_mfa_secret_is_binary_and_not_editable(self) -> None:
        """It holds a ports.crypto.Ciphertext blob, never a bare seed."""
        assert isinstance(field(User, "mfa_secret"), models.BinaryField)
        assert field(User, "mfa_secret").editable is False

    @pytest.mark.parametrize(
        "name",
        [
            "public_id",
            "email",
            "phone_e164",
            "status",
            "email_verified_at",
            "phone_verified_at",
            "mfa_secret",
            "failed_login_count",
            "locked_until",
            "last_login",
            "deleted_at",
            "created_at",
            "updated_at",
        ],
    )
    def test_every_specified_column_exists(self, name: str) -> None:
        assert field(User, name) is not None


class TestUniquenessIsPartial:
    """§7.7: "Soft-deleted rows excluded from uniqueness … Re-registration
    after account closure remains possible"."""

    def test_email_uniqueness_excludes_deleted_rows(self) -> None:
        assert "user_email_unique_alive" in constraint_names(User)

    def test_phone_uniqueness_excludes_deleted_and_null(self) -> None:
        assert "user_phone_unique_alive" in constraint_names(User)

    def test_the_email_constraint_is_actually_conditional(self) -> None:
        constraint = next(c for c in User._meta.constraints if c.name == "user_email_unique_alive")
        assert constraint.condition is not None

    def test_email_carries_no_unconditional_unique_index(self) -> None:
        """Regression. `unique=True` on the field emits a second,
        unconditional index that sits alongside the partial one and silently
        defeats it — a closed account would keep its address forever. Caught
        by inspecting the real schema, not by a failing behaviour.
        """
        assert field(User, "email").unique is False

    def test_the_same_applies_to_the_phone_column(self) -> None:
        assert field(User, "phone_e164").unique is False


class TestIsActiveIsDerived:
    """Two sources of truth for "may this account log in" is one too many."""

    def test_an_active_user_is_active(self) -> None:
        assert User(status=UserStatus.ACTIVE).is_active

    @pytest.mark.parametrize(
        "status", [UserStatus.PENDING, UserStatus.SUSPENDED, UserStatus.CLOSED]
    )
    def test_every_other_status_is_inactive(self, status: UserStatus) -> None:
        assert not User(status=status).is_active

    def test_a_soft_deleted_active_user_is_inactive(self) -> None:
        from django.utils import timezone

        assert not User(status=UserStatus.ACTIVE, deleted_at=timezone.now()).is_active


class TestEmailNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("  Alice@Example.COM ", "alice@example.com"),
            ("ALICE@EXAMPLE.COM", "alice@example.com"),
            ("alice@example.com", "alice@example.com"),
        ],
    )
    def test_casefolds_and_strips(self, raw: str, expected: str) -> None:
        assert User.normalise_email(raw) == expected


class TestRoleSeedMatchesTheEnum:
    def test_the_seed_covers_every_role_in_the_shared_vocabulary(self) -> None:
        """A role in the enum but not the table grants nothing at runtime; a
        role in the table but not the enum cannot be checked at all."""
        assert {code for code, _ in SEED.ROLES} == {r.value for r in RoleEnum}

    def test_the_seed_has_no_duplicates(self) -> None:
        codes = [code for code, _ in SEED.ROLES]
        assert len(codes) == len(set(codes))

    def test_every_role_has_a_human_name(self) -> None:
        for code, name in SEED.ROLES:
            assert name and name != code


class TestGrantsSurviveTheirGranter:
    def test_granted_by_is_set_null_not_cascade(self) -> None:
        """Removing an administrator must not delete the record of what they
        granted."""
        assert field(UserRole, "granted_by").remote_field.on_delete is models.SET_NULL

    def test_a_role_in_use_cannot_be_deleted(self) -> None:
        assert field(UserRole, "role").remote_field.on_delete is models.PROTECT

    def test_a_user_cannot_hold_the_same_role_twice(self) -> None:
        assert "user_role_unique" in constraint_names(UserRole)


class TestSession:
    def test_the_jti_is_unique(self) -> None:
        assert field(Session, "jti").unique

    def test_the_family_is_indexed(self) -> None:
        """Revoking a family is a lookup by family_id, on the hot path of a
        reuse detection."""
        assert field(Session, "family_id").db_index

    def test_a_live_session_is_neither_revoked_nor_superseded(self) -> None:
        assert Session().is_live
        assert not Session(superseded_by="00000000-0000-0000-0000-000000000001").is_live

    def test_it_carries_the_investigation_context(self) -> None:
        """§41.13 — and an unrecognised city is what makes "alert the user"
        actionable rather than alarming."""
        assert field(Session, "ip") is not None
        assert field(Session, "user_agent") is not None


class TestDevice:
    def test_a_push_token_is_unique_among_live_rows(self) -> None:
        """Two live rows for one token would send one user's itinerary to
        another user's handset after a device changes hands."""
        assert "device_push_token_unique_live" in constraint_names(Device)

    def test_the_constraint_excludes_revoked_rows(self) -> None:
        constraint = next(
            c for c in Device._meta.constraints if c.name == "device_push_token_unique_live"
        )
        assert constraint.condition is not None


class TestOneTimeToken:
    def test_only_the_hash_is_stored(self) -> None:
        """The plaintext lives in the email and nowhere else, so a database
        disclosure does not hand over every pending account."""
        assert field(OneTimeToken, "token_hash").unique
        with pytest.raises(FieldDoesNotExist):
            field(OneTimeToken, "token")

    def test_it_has_an_expiry(self) -> None:
        assert field(OneTimeToken, "expires_at").db_index


class TestExternalIdentifiersAreUuids:
    """§7.2: "Sequential integers are never returned to clients"."""

    @pytest.mark.parametrize("model", [User, TouristProfile, Device])
    def test_client_addressable_models_carry_a_public_id(self, model: type[models.Model]) -> None:
        assert isinstance(field(model, "public_id"), models.UUIDField)
        assert field(model, "public_id").unique
