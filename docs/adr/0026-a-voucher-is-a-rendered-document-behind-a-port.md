# ADR 0026 — A voucher is a rendered document behind a port

**Status:** Accepted · **Date:** 2026-09-14 · **Phase:** 7

## Context

§41.8's acceptance criterion is one sentence: "vouchers generate for every
confirmed booking". §9.3.5 adds `POST /bookings/{id}/voucher | T |
Generate/download PDF voucher`. That is the whole specification.

The SRS never says what a voucher contains, how it is laid out, where it is
stored, how long it is kept, whether it is made eagerly at confirmation or on
request, whether its URL is signed, or what re-issuing one does. §27.9 lets an
administrator "re-issue a voucher", which implies a voucher is a thing with an
identity that can be issued more than once.

What exists: `EmailPort` and `StoragePort` are declared, registered and faked.
There is no document renderer of any kind — a repository-wide search for
`voucher` or `pdf` finds no production code — and hard rule 13 puts any vendor
library behind a port with a fake. `IMPLEMENTATION-PLAN.md` risk R7 already
records that §41.10 as amended makes an emailed PDF an MVP obligation.

And the storage in every current environment is `FakeStorage`: an in-memory
dictionary inside one process. A voucher written by the request that confirmed a
booking is invisible to the next request if it lands on another worker or after
a reload. A presigned URL from it points at `fake-storage.local`.

## Decision

### 1. Rendering is a port: `DocumentPort.render_voucher(content) -> bytes`

`ports/document.py` declares the protocol and a `VoucherContent` value object;
`FakeDocuments` in `ports/fakes.py` returns a small deterministic document so
tests can assert on content without parsing PDF. The real adapter lives in
`apps/booking/adapters/` and is the only place the PDF library is imported.

`document` joins `ports_registry._FAKES`, and
`test_ports_registry.py::test_the_fakes_are_exactly_these` widens by exactly one
entry, with the reason written beside it. Unlike payment and routing, a
renderer has no external side effect and no money behind it, so a fake carries
none of the risk that keeps those two unregistered.

The adapter is selected by `PORT_ADAPTERS["document"]`, read from
`DOCUMENT_ADAPTER`, and development and production name the real renderer. The
fake is for tests, where a PDF byte stream is the wrong thing to assert against.

### 2. What a voucher says

Inferred from §41.10, which lists what the emailed itinerary must carry, applied
to one booking:

- booking reference and trip reference
- the service: activity name and departure, or transfer origin → destination,
  vehicle class and pickup time
- date and time in the destination's timezone, with the zone named
- party size
- the provider's trading name and contact
- the meeting or pickup point
- the amount paid for the component and its currency
- the cancellation policy in plain words, from the booking's own snapshot
- the platform support number
- the issue number and time

It carries **no** pickup PIN and **no** driver identity: neither exists before
the driver phases, and a voucher that printed a placeholder would be read as
real. Both are added when they exist. It carries no commission, for the reason
§24.21 gives.

Every field comes from the booking row, its subtype and the resolved facts
captured with it. Nothing is read from the live catalogue at render time, so a
listing renamed after confirmation does not change a voucher already issued.

### 3. A voucher is a record; the file is derived from it

`booking_voucher`: `booking_id`, `issue_number`, `storage_key`, `sha256`,
`size_bytes`, `issued_at`, `issued_by_user_id NULL`, `reason NULL`. UNIQUE on
`(booking_id, issue_number)`.

Issue 1 is created inside the transaction that confirms the booking, which is
what satisfies §41.8 for every confirmation path at once. Rendering is local
computation, not an external call, so hard rule 11 is not engaged; the storage
write happens after commit.

**The render is deterministic**: PDF creation and modification dates are taken
from `issued_at`, not the clock, and the document ID is derived from the booking
reference and issue number. So the same record always yields the same bytes, and
`sha256` is a check rather than a label.

### 4. The endpoint returns the document, not a link

`POST /bookings/{id}/voucher` responds `200 application/pdf` with the latest
issue's bytes. It reads through `StoragePort` and, if the object is absent,
re-renders from the record **and refuses to serve the result unless its hash
equals the stored `sha256`**. A voucher is a document someone may already have
printed; one that could quietly change on a second download is worse than an
error.

Returning bytes rather than a presigned URL is what makes this work while
storage is an in-process fake. When a real object store arrives the endpoint
may redirect to a signed URL instead; the record and the hash are unchanged.

It is a POST, as §9.3.5 gives it, and is `@idempotent` only in the trivial
sense that it creates nothing: the first issue exists from confirmation.

### 5. Re-issue is an audited administrative action

§27.9's "re-issue a voucher" creates issue *n + 1* with a required reason and an
audit entry. Earlier issues are kept, not overwritten — the same append-only
discipline as the status history, for the same reason.

### 6. Email carries it, when there is something to email

`BookingConfirmed` enqueues an email with the voucher attached, after commit,
through `EmailPort`. In development `EMAIL_ADAPTER` is the fake and nothing is
sent. The full §41.10 itinerary PDF — every voucher plus the day-by-day plan and
calendar entries — is the trip-level document and arrives with `TRIP_CONFIRMED`
in Phase 8, when a trip can actually become confirmed by payment.

## Consequences

**§41.8 holds by construction.** A booking cannot reach CONFIRMED without its
first voucher record, because both are written by one transaction.

**A tourist can download a real PDF in development**, because the renderer is
real there and the endpoint does not depend on storage surviving a restart.

**One new dependency, confined.** The PDF library is imported in one adapter
module. Swapping it touches that module and nothing that calls the port.

**The voucher omits what the product cannot yet promise.** Driver identity and
the pickup PIN are absent until they exist, rather than present and wrong.
