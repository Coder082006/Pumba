# Phase 2 — Identity and Authentication

**Status:** Proposed — design gate, awaiting approval before views are written
**SRS:** §37.2, §5.2, §7.2, §7.5.1–7.5.2, §9.4.1–9.4.2, §9.6, §24.3–24.5, §30.2–30.4
**Acceptance:** TC-001, 002, 003, 010, 011, 012, 013 + the TC-2xx authorisation matrix

---

## 0. Seven things the SRS does not settle

Three need your decision because different answers produce materially different
work. Four I will proceed on with the stated recommendation unless you say
otherwise.

### Q1 — Where does the refresh token live? ⚠️ **decision needed**

§9.4.2 returns `refresh_token` in the response body. §30.4 says *"the web
portals use bearer tokens held in memory with refresh tokens in HttpOnly,
Secure, SameSite=Strict cookies, and therefore carry CSRF double-submit tokens
on state-changing requests."* Both cannot be the default, and the brief forbids
the API knowing which client is calling.

**Recommendation.** One endpoint, no client branching in business logic:

- `POST /auth/login` **always** returns the refresh token in the body (§9.4.2,
  client-agnostic — this is what Flutter and the driver app will use).
- When the request `Origin` matches a configured web origin, it **additionally**
  sets the `HttpOnly; Secure; SameSite=Strict` cookie (§30.4).
- `POST /auth/refresh` accepts the token from the body **or** the cookie.
- CSRF double-submit is enforced only on the cookie path, since only that path
  is ambient-credential-bearing.

The alternative — a `client_type` request field — puts client knowledge into the
API and I would rather not, given the constraint we have held since Phase 1.

### Q2 — `audit_log` is owned by `administration` (L7). Phase 2 must audit. ⚠️ **decision needed**

§37.2 requires *"audit logging of all authentication events"* and §41.13
requires actor, role, IP and request id. But §6.4 gives `audit_log` to
`administration`, which is L7, and `identity` is L0 — so `identity` cannot
import it. This is the same shape as issue S1, which we resolved for
`system_setting` by splitting read from write.

**Recommendation.** Mirror S1 exactly:

- `apps/common/audit.py` is the **write port** — `record_audit(...)`, a leaf
  every module may import, with a pluggable provider.
- Create `administration.models.AuditLog` and its service **now**: one model,
  one service, one migration. No console, no UI, no other `administration`
  entity — those stay in their own phase.

This is a deliberate, minimal step outside "identity module only", and I am
flagging it rather than taking it silently. The alternative is to emit domain
events and have `administration` subscribe later, but then Phase 2 ships with no
audit trail at all and fails its own acceptance criterion.

### Q3 — Social sign-in ⚠️ **decision needed**

§30.2 says *"social sign-in (Google, Apple) is supported for tourists via OIDC
with server-side token verification"*, and §24.3/§24.4 both show the buttons.
§37.2's feature list does not mention it.

**Recommendation: defer.** It needs OAuth client registration and consent-screen
review at both vendors, which is a commercial/legal lead time that is not in
Appendix D at all. The Phase 2 registration and login screens ship without the
buttons. If you want it in Phase 2, it needs to start this week alongside D1/D2.

### Q4 — `session` and `device` have no schema anywhere in the SRS

§6.4 lists both as tables `identity` owns. §7.5 specifies `user` (7.5.1) and
`tourist_profile` (7.5.2) and then moves to `provider` — there is no `session`
or `device` table, and neither appears in the §7.3 ERD either. `role` and
`user_role` exist only as ERD boxes (`role`: id, code, name; `user_role`:
role_id, user_id, granted_at).

**Proceeding:** I design all four against §7.2's conventions and record the
schema as an ADR, so the invention is visible rather than buried in a migration.

### Q5 — Appendix B has no keys for anything in this phase

§30.2 gives the lockout policy (10 failures / 15 minutes, exponential to 1 hour)
and §9.6 gives eight rate limits, but Appendix B — *"the practical expression of
NFR-M07"* — registers none of them. Hard rule 5 forbids constants.

**Proceeding:** ~14 new keys in `SETTINGS_REGISTER`, flagged as an Appendix B
extension in the ADR rather than smuggled in.

### Q6 — `mfa_secret` is "encrypted" with no key management decided

§7.5.1 has `mfa_secret BYTEA` and §30.4 mandates envelope encryption with keys
in a managed secrets store, rotated annually. No KMS provider is selected, and
unlike payment and routing there is no Appendix D entry for it.

**Proceeding:** a `CryptoPort` in `ports/` with an AEAD interface, a local
key-from-settings adapter for dev and CI, and a fake. Same shape as every other
external dependency. I will propose it as **D8** — it blocks production launch,
not this phase.

### Q7 — The breached-password list has no named source, and no failure policy

§30.2 requires the check but names no provider and does not say what happens
when the provider is unreachable.

**Recommendation.** `BreachedPasswordPort` with a k-anonymity range lookup, and
a seeded local fake so CI never makes a network call. On the failure policy —
and this is a real security decision, not a detail:

- **Registration and password reset: fail closed.** Refuse to set a password we
  could not check. The user retries in a minute; nobody is locked out of
  anything they already have.
- **Login: fail open.** Never deny an existing user access to their own account
  because a third party is down.

---

## 1. Domain-layer surface

`apps/identity/domain/` — pure functions over value objects. No ORM, no Django,
no I/O, no `datetime.now()`. Every function that needs the current time takes
`now` as a parameter, which is also what makes the lockout and TOTP tests
deterministic. This layer carries the 95% coverage gate.

### `roles.py` — the §5.2 role model

```python
class Role(StrEnum):
    TOURIST, DRIVER, PROVIDER_OWNER, PROVIDER_STAFF, SUPPORT_AGENT,
    FINANCE_OFFICER, CATALOGUE_ADMIN, COMPLIANCE_ADMIN, SUPER_ADMIN

class Permission(StrEnum):        # operation classes, not CRUD verbs
    PROFILE_WRITE, TRIP_WRITE, BOOKING_CREATE, PAYMENT_INITIATE, ...

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]]   # transcribed §5.2

def permissions_for(roles: frozenset[Role]) -> frozenset[Permission]: ...
def mfa_mandatory(roles: frozenset[Role]) -> bool: ...
```

`mfa_mandatory` is the §30.2 rule — true for `PROVIDER_*` and every
administrative role, false for `TOURIST` and `DRIVER`. It is a pure function of
the role set, which is what lets the "a provider without TOTP cannot reach the
console" test be a unit test rather than an integration test.

### `principal.py` — what the whole authorisation layer works against

```python
@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int                  # internal, never serialised
    user_public_id: UUID
    roles: frozenset[Role]
    tourist_id: int | None
    driver_id: int | None
    provider_id: int | None
    is_email_verified: bool
    mfa_satisfied: bool

    def has(self, permission: Permission) -> bool: ...
```

A plain frozen dataclass, not a Django user. This is deliberate: it means every
permission and ownership test runs without a database, and it means the
authorisation logic cannot accidentally reach through a relation.

### `ownership.py` — the control, in one place

This is the piece you asked to see. §30.3 is explicit that the check is *"queryset
filtered by principal at the repository layer — never fetch then compare, which
leaks existence"*. So ownership is **not** a DRF `has_object_permission` hook.
It is a declaration that the data-access layer turns into a filter.

```python
class Resource(StrEnum):
    TRIP, BOOKING, PAYMENT, ASSIGNMENT, LISTING, PROFILE, DEVICE, ...

class Scope(StrEnum):
    OWNED         # restricted to rows matching the principal
    GLOBAL_READ   # every row, read only  (SUPPORT_AGENT)
    GLOBAL        # every row             (FINANCE_OFFICER, *_ADMIN)
    NONE          # no access

@dataclass(frozen=True, slots=True)
class OwnershipRule:
    """One cell of the §5.2 'Ownership predicate' column."""
    scope: Scope
    principal_attr: str | None    # "tourist_id" | "provider_id" | "driver_id"
    row_field: str | None         # the column on the target table

OWNERSHIP: Mapping[tuple[Role, Resource], OwnershipRule]
```

The filter is itself a value object, so the domain never touches a `QuerySet`:

```python
@dataclass(frozen=True, slots=True)
class Filter:
    kind: Literal["ALLOW_ALL", "DENY_ALL", "EQUALS", "ANY_OF"]
    field: str | None = None
    value: object | None = None
    branches: tuple[Filter, ...] = ()

    @classmethod
    def allow_all(cls) -> Filter: ...
    @classmethod
    def deny_all(cls) -> Filter: ...
    @classmethod
    def equals(cls, field: str, value: object) -> Filter: ...
    @classmethod
    def any_of(cls, *branches: Filter) -> Filter: ...


def ownership_filter(principal: Principal, resource: Resource,
                     *, write: bool) -> Filter:
    """The rows this principal may reach on this resource."""
```

Four semantics I want on the record before this is used fourteen times, because
each one is a place this class of control usually goes wrong:

1. **Fail closed.** No rule for `(role, resource)` yields `DENY_ALL`, never
   `ALLOW_ALL`. The default for an unmapped pair is denial, and a test asserts
   the map is total over `Role × Resource` so a new resource cannot be
   silently unprotected.
2. **Multi-role principals union, they do not intersect.** A user who is both
   `TOURIST` and `DRIVER` sees their own trips *and* their own assignments.
   Intersecting would be the safe-looking wrong answer and would break real
   accounts.
3. **`GLOBAL_READ` degrades to `DENY_ALL` when `write=True`.** That is the
   entire difference between `SUPPORT_AGENT` and `FINANCE_OFFICER` in §5.2, and
   encoding it as a flag on the lookup rather than two maps keeps it visible.
4. **`DENY_ALL` becomes `queryset.none()`, which becomes 404.** This is how
   "a foreign principal receives 404, not 403" is achieved structurally rather
   than by remembering to raise the right exception. There is no code path in
   which a foreign principal reaches an object and is then rejected.

The translation to the ORM lives outside the domain, in `apps/common/scoping.py`
so all fourteen modules share one implementation:

```python
def apply_filter(qs: QuerySet, f: Filter) -> QuerySet: ...   # the only Q() builder
def scoped(qs, principal, resource, *, write=False) -> QuerySet: ...
```

### `passwords.py`

```python
@dataclass(frozen=True, slots=True)
class PasswordViolation:
    code: str        # TOO_SHORT | BREACHED | EQUALS_EMAIL_LOCAL_PART
    message: str

def validate_password(password: str, *, email: str, min_length: int,
                      is_breached: bool) -> tuple[PasswordViolation, ...]: ...
```

Returns violations rather than raising, so the serializer can map each to a
`details[].field` path per §24.3. `is_breached` arrives as a **bool computed by
the application layer** — the port call stays out of the domain, and the rule
stays testable without a network.

### `lockout.py` — §30.2

```python
@dataclass(frozen=True, slots=True)
class LockoutPolicy:
    threshold: int          # 10
    window: timedelta       # 15 minutes
    base_duration: timedelta
    max_duration: timedelta # 1 hour

@dataclass(frozen=True, slots=True)
class LockoutDecision:
    is_locked: bool
    locked_until: datetime | None
    failed_count_after: int
    notify_owner: bool      # §30.2 requires notifying the account owner

def register_failure(*, failed_count: int, first_failure_at: datetime | None,
                     lockout_count: int, now: datetime,
                     policy: LockoutPolicy) -> LockoutDecision: ...
def is_locked(*, locked_until: datetime | None, now: datetime) -> bool: ...
```

Exponential: `min(base * 2**lockout_count, max_duration)`. The window rolls —
failures older than `window` do not count, which is what "10 failures in 15
minutes" actually means and is the part that is usually implemented as a plain
counter by mistake.

### `mfa.py` — RFC 6238, stdlib only

```python
def totp_code(secret: bytes, *, at: datetime, step_seconds: int = 30,
              digits: int = 6) -> str: ...
def verify_totp(secret: bytes, code: str, *, at: datetime,
                drift_steps: int = 1, ...) -> bool: ...   # hmac.compare_digest
def provisioning_uri(*, secret: bytes, account: str, issuer: str) -> str: ...
```

Hand-rolled on `hmac`/`struct`/`base64` rather than a dependency, and tested
against the **published RFC 6238 test vectors** — for a security primitive I would
rather have proof of correctness than a transitive dependency. Comparison is
`hmac.compare_digest`. It is ~25 lines and fully pure.

### `tokens.py` — refresh rotation and reuse detection, §30.2

```python
class FamilyAction(StrEnum):
    ROTATE, REVOKE_FAMILY

@dataclass(frozen=True, slots=True)
class TokenView:              # what the repository loads, no ORM
    jti: UUID
    family_id: UUID
    is_revoked: bool
    superseded_by: UUID | None
    expires_at: datetime

@dataclass(frozen=True, slots=True)
class RotationDecision:
    action: FamilyAction
    reason: str
    alert_owner: bool

def evaluate_refresh(token: TokenView | None, *, now: datetime) -> RotationDecision: ...
```

The reuse rule, stated so the test can be written before the code: a presented
refresh token that is **expired**, **revoked**, **unknown**, or **already
superseded** revokes the entire family and alerts the owner. Only a token that
is current, unrevoked and unexpired rotates. "Already superseded" is the replay
case — the attacker presents a token the legitimate user already exchanged — and
it gets its own explicit test, per your note.

---

## 2. Permission-class design

Two independent checks, per §30.3, enforced in two different places on purpose.

### Check 1 — role, declarative per view

```python
class HasPermission(BasePermission):
    """Does this principal's role permit this operation class?"""
    required: ClassVar[Permission]

    @classmethod
    def for_(cls, permission: Permission) -> type[BasePermission]: ...

    def has_permission(self, request, view) -> bool:
        return request.principal.has(self.required)
```

Used as `permission_classes = [IsAuthenticated, HasPermission.for_(Permission.TRIP_WRITE)]`.

Two companions:

- `EmailVerified` — §30.2, *"email verification required before booking"*.
- `MfaSatisfied` — returns false when `mfa_mandatory(principal.roles)` and
  `not principal.mfa_satisfied`. This is what stops a provider account without
  TOTP reaching the console, and because it keys off the role set rather than a
  URL prefix, it cannot be bypassed by finding an unguarded provider endpoint.

### Check 2 — ownership, structural, not a permission class

```python
class ScopedQuerysetMixin:
    ownership_resource: ClassVar[Resource]

    def get_queryset(self):
        return scoped(super().get_queryset(), self.request.principal,
                      self.ownership_resource,
                      write=self.request.method not in SAFE_METHODS)
```

Deliberately **not** `has_object_permission`. DRF's object hook runs *after*
`get_object()` has already fetched the row, which is precisely the fetch-then-
compare pattern §30.3 forbids. Filtering the queryset means the row is never
loaded and `get_object()` raises `Http404` on its own.

### How it is prevented from being forgotten fourteen times

Three mechanisms, because a convention that relies on memory is not a control:

1. **URL-conf enumeration test.** Walks the resolved URL conf, and for every
   view asserts it either declares `ownership_resource` or appears on an
   explicit, commented allow-list of non-row endpoints (health, login,
   register, password reset, OpenAPI). Adding an endpoint without a decision
   fails CI — this is the same test that satisfies "no endpoint is
   unintentionally public", asserted by enumeration and not by inspection.
2. **Totality test.** `OWNERSHIP` is asserted total over `Role × Resource`, so a
   new `Resource` member fails the build until every role's access is stated.
3. **`has_object_permission` tripwire.** The project base view defines it to
   raise, so reintroducing fetch-then-compare is a loud failure in development
   rather than a quiet regression in production.

### The authorisation matrix harness

A single parametrised test generating `(endpoint × role × ownership)` cases from
the URL conf and `ROLE_PERMISSIONS` — so it grows automatically as later phases
add endpoints, which is what §37.2 means by *"runs green across every endpoint
then existing"*. Each cell asserts one of: 200/201, 403 (role fails), 404
(ownership fails — never 403), or 401.

---

## 3. Models

`identity` (L0), all per §7.2 conventions on `BaseModel`/`SoftDeleteModel`:

| Model | Source |
|---|---|
| `User` | §7.5.1 verbatim; `AUTH_USER_MODEL`, `CITEXT` email, partial unique indexes |
| `TouristProfile` | §7.5.2 verbatim; `passport_reference` via `CryptoPort` |
| `Role`, `UserRole` | §7.3 ERD only — `code`, `name`; `granted_at`, `granted_by` |
| `Session` | **designed** (Q4) — refresh family: `jti`, `family_id`, `superseded_by`, `revoked_at`, `expires_at`, `ip`, `user_agent` |
| `Device` | **designed** (Q4) — push: `platform`, `push_token`, `last_seen_at`, unique on active token |
| `OneTimeToken` | **designed** — email verification and password reset; hashed, single-use, TTL from settings |
| `AuditLog` | `administration`, per Q2 |

Plus the §7.2 `updated_at` trigger: a `RunSQL` migration creating a reusable
`set_updated_at()` function and attaching it, with a helper so every later
phase's tables get it without re-deriving the SQL.

`AUTH_USER_MODEL` lands in the first migration and the local database is
recreated, as you noted.

---

## 4. Endpoints

`POST /auth/register` · `POST /auth/verify-email` · `POST /auth/login` ·
`POST /auth/refresh` · `POST /auth/logout` · `POST /auth/password/forgot` ·
`POST /auth/password/reset` · `POST /auth/mfa/enrol` · `POST /auth/mfa/verify` ·
`GET|PATCH /me` · `POST /me/devices` · `DELETE /me/devices/{public_id}`

Rate limits from §9.6 as Redis token buckets in `apps/common/throttling.py`,
keyed by principal when authenticated and IP otherwise, with every limit read
from `system_setting`.

**Non-enumeration** is a design constraint on three of these, not a
post-hoc fix. Login runs the Argon2 verify against a **dummy hash** when the
email is unknown, so the work factor is paid either way and the timing
distributions overlap; the response body and status are byte-identical.
`/auth/password/forgot` returns the same 202 regardless (§24.5). TC-013 asserts
body equality, and a timing test asserts the medians sit within a tolerance
band.

---

## 5. Commit sequence

One logical change per commit, verified before committing.

1. `feat(identity)`: role model, permissions map, `mfa_mandatory` — domain only
2. `feat(identity)`: `Principal`, ownership rules, `Filter` — domain only
3. `feat(common)`: `scoped()`/`apply_filter()` + totality and fail-closed tests
4. `feat(identity)`: password policy, lockout, TOTP (RFC 6238 vectors), token rotation — domain only
5. `feat(common)`: audit write port; `feat(administration)`: `AuditLog` + service
6. `feat(common)`: settings register extension (Appendix B gap)
7. `feat(ports)`: `CryptoPort`, `BreachedPasswordPort` + fakes
8. `feat(identity)`: models, `AUTH_USER_MODEL`, `updated_at` trigger migration
9. `feat(identity)`: repositories and selectors — all reads scoped
10. `feat(identity)`: services — register, verify, login, refresh, logout, reset, MFA
11. `feat(identity)`: permission classes, `ScopedQuerysetMixin`, tripwire
12. `feat(identity)`: serializers, views, urls **← the gate you set is before this**
13. `feat(common)`: rate limiting per §9.6
14. `test(api)`: authorisation matrix harness + URL-conf enumeration test
15. `feat(web-tourist)`: register, login, forgot-password (§24.3–24.5)
16. `feat(web-console)`: login with mandatory TOTP
17. `docs`: ADRs for Q1–Q7; OpenAPI regenerated

---

## 6. Test plan

| SRS | Assertion |
|---|---|
| TC-001 | 201; `PENDING`; verification email sent; audit row written |
| TC-002 | 409 `EMAIL_ALREADY_REGISTERED`; no second row |
| TC-003 | 422; breach-list rejection; no user created |
| TC-010 | 200; both tokens; `last_login_at` set |
| TC-011 | 401 `INVALID_CREDENTIALS`; counter incremented |
| TC-012 | 423 with unlock time; owner notified |
| TC-013 | Response byte-identical to TC-011; timing within tolerance |
| §30.2 | **Replay:** a superseded refresh token revokes the whole family and alerts |
| §30.2 | Provider without TOTP is refused the console |
| §30.3 | Foreign principal → 404 on every owned resource |
| §37.2 | URL-conf enumeration: no unintentionally public endpoint |
| §6.5 r5 | `services.py` returns DTOs and primitives, never ORM instances |

Gates unchanged: ruff, mypy, import-linter 31/31, 80% overall, **95% on `domain/`**.

---

## 7. Not in this phase

No catalogue, provider, listing or booking model (L1+, Phase 3+). No social
sign-in pending Q3. No `administration` entity other than `AuditLog`. No Flutter
tourist screens — v1.1 per ADR 0002. No console beyond login.
