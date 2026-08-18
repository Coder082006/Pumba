# ADR 0008 — The refresh token goes in the body, and in a cookie for web origins

**Status:** Accepted · **Date:** 2026-08-18 · **Phase:** 2 · **Issue:** Q1

## Context

The SRS specifies the refresh token's transport twice, differently.

**§9.4.2** — `POST /auth/login` responds `200` with *"access_token (15 min),
refresh_token (30 days, rotating), token_type, expires_in, and principal"*.
The refresh token is in the response body.

**§30.4** — *"The web portals use bearer tokens held in memory with refresh
tokens in HttpOnly, Secure, SameSite=Strict cookies, and therefore carry CSRF
double-submit tokens on state-changing requests. The mobile apps use bearer
tokens and are not CSRF-exposed."*

Both are right for their client. A cookie is the correct store for a browser,
because JavaScript cannot read an `HttpOnly` cookie and XSS therefore cannot
exfiltrate the long-lived credential. A cookie is useless to Flutter, which
has no ambient cookie jar tied to an origin and stores the token in platform
secure storage instead.

The brief's constraint cuts across both: *"The API must stay strictly
client-agnostic… When we add Flutter, zero backend changes should be
required."* An endpoint that behaves differently because it was told which
client is calling has client knowledge in it.

## Decision

One endpoint, no client-type parameter, no branching in business logic.

1. **`POST /auth/login` always returns `refresh_token` in the body.** This is
   §9.4.2 as written, and it is what the Flutter driver app (MVP) and the
   Flutter tourist app (v1.1) will use.

2. **When the request `Origin` matches a configured web origin, the response
   *additionally* sets the cookie** — `HttpOnly; Secure; SameSite=Strict;
   Path=/api/v1/auth`. This is §30.4 as written.

3. **`POST /auth/refresh` accepts the token from the body or the cookie**,
   body first. Both paths run the identical rotation and reuse-detection
   logic; only the transport differs.

4. **CSRF double-submit is enforced only on the cookie path**, because only an
   ambient credential is CSRF-exposed. A request presenting the token in the
   body is proving possession, which is what CSRF cannot do.

The origin allow-list is the same `CORS_ALLOWED_ORIGINS` the portals already
require, so there is no second list to keep in step.

## Why not the alternatives

**A `client_type` field on the request.** Puts client knowledge into the API
and makes the security posture a client-supplied claim — an attacker sends
`client_type: "mobile"` and opts out of the cookie. Rejected on both counts.

**Cookie only, for everyone.** Breaks Flutter, and therefore breaks the driver
app, which is MVP.

**Body only, for everyone.** Contradicts §30.4 and leaves a 30-day credential
in browser-reachable memory or storage, where one XSS takes it.

**Separate `/auth/login/web` and `/auth/login/mobile` endpoints.** Two code
paths through the most security-sensitive route in the system, which will
drift. The whole point of one endpoint is that reuse detection cannot be
correct on one path and wrong on the other.

## Consequences

**The web client never reads the refresh token**, even though it is in the
body it received. That is a discipline the browser cannot enforce for us, so
it is a review checkpoint on `apps/web-tourist` and `apps/web-console`: the
access token goes in memory, the refresh token is ignored and the cookie does
the work.

Returning it in the body to a browser is a real, accepted cost — it is
momentarily readable by script on that response. The alternative is a
client-type switch, which is worse, and the exposure ends when the response is
discarded rather than lasting 30 days in storage.

**Adding Flutter requires no backend change**, which is the property the
brief asked for and the property that lets the driver app ship on its own
schedule.

**Logout must clear both.** Revoking the family server-side and expiring the
cookie are two separate actions, and doing only the first leaves a cookie the
browser keeps sending.

## Related

- `docs/PHASE-2-PLAN.md` §0 Q1.
- SRS §9.4.2, §30.4.
