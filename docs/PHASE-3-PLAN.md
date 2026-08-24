# Phase 3 — Zanzibar Tourism Catalogue

**Status:** In progress.
**SRS:** §37.3, §4.1–4.3, §6.4, §7.5.6–7.5.7, §7.6, §14, §15, §16, §9.3.2 (API-02),
§24.7–24.13, §27.8, §29 (NFR-P01), §41.12, Appendix C — **as amended to v1.2**
**Acceptance:** ~~TC-020~~, TC-021, TC-902, §41.12 destination independence, Lighthouse gates

> **Amended 2026-08-21 — [ADR 0013](adr/0013-accommodation-is-a-location-reference-in-v1.md).**
> Accommodation is no longer a bookable product. It is a curated location
> reference, and a STAY itinerary item is a *stay anchor*: location and dates,
> no provider, no price, no booking, no inventory. `room_type` and
> `room_availability` are v2 and have left the v1 schema.
>
> This partly reverses **Q2** below, which brought `room_availability` forward
> so TC-020 could pass. TC-020 is void in v1 and is amended in the SRS rather
> than deleted; it returns with v2. Q2 is left in place, struck through, because
> the reasoning that produced ADR 0011 and ADR 0012 came out of it.
>
> The sections below are amended in place. Where a deliverable is deferred it is
> marked, not deleted, so the commit numbering keeps resolving.

---

## 0. Nine things the SRS does not settle

Five change what gets built and need a decision. Four I will proceed on with the
stated recommendation unless you say otherwise.

### Q1 — Enabling GeoDjango breaks the local and CI toolchains ⚠️ **decision needed**

ADR 0004 deferred `django.contrib.gis` to this phase on the reasoning that the
image and the database were already correct, so enabling it would be "two
settings lines". That was true of the *container*. It is not true of the two
places the test suite actually runs:

```
apps/api $ uv run python -c "from django.contrib.gis.gdal import gdal_version"
ImproperlyConfigured: Could not find the GDAL library
```

- **This Windows host** has no GDAL, GEOS or PROJ. `make test` runs `uv run
  pytest` directly on the host, so the moment `django.contrib.gis` enters
  `INSTALLED_APPS`, every backend test fails at import — including the ones with
  no geometry in them.
- **CI** runs `uv sync` on a bare `ubuntu-latest` runner, not inside
  `api.Dockerfile`. The runner has no GDAL either, so `backend-lint`,
  `module-boundaries` and `backend-test` all break the same way.

There is no pure-Python fix; GeoDjango binds native libraries.

**Recommendation.** Two changes, both small and both permanent:

1. CI gains one `apt-get install gdal-bin libgdal-dev libgeos-dev libproj-dev`
   step in the three backend jobs. Correct, and it makes CI match the image.
2. `make test` / `pnpm test:api` run the backend suite **inside the api
   container** (`docker compose run --rm api pytest`), which already carries the
   libraries by ADR 0004's own design. A `make test-host` escape hatch remains
   for anyone who has GDAL locally.

The alternative — hand-installing OSGeo4W on every developer machine and pinning
`GDAL_LIBRARY_PATH` per platform — makes the setup instructions
platform-specific, which §37.1's "one command" promise does not survive.

This supersedes the closing paragraph of ADR 0004, so ADR 0004 gets a
superseded-in-part note and ADR 0009 records the real cost.

### ~~Q2 — TC-020 cannot pass without `room_availability`~~ — **superseded by ADR 0013**

**Resolved by removing the question.** TC-020 is void in v1: there is no
accommodation search with availability, because there is no availability. Option
B was taken and then partly reversed — `room_availability` shipped in commit 19
and left again in `inventory/0002`. The reasoning was not wasted: it produced
ADR 0011 (inventory owns the counter tables) and ADR 0012 (cross-module
references are ids), both of which still hold and both of which now govern
`activity_departure` alone. The original question follows.

Your scope says accommodation is "models and reads only — `room_availability`,
holds and the authoritative availability check are Phase 5". Your acceptance
says TC-020 passes. TC-020 is:

> Search accommodation | Property with availability | Search valid dates | 200;
> **only room types available every night**

Without `room_availability` there is no availability data of any kind, so the
"available every night" half of TC-020 is not merely unverified — it is
unexpressible. §14.2 has the same problem: the nightly rate is
`room_availability.rate_override` *else* `room_type.base_rate`, so a stay total
computed in Phase 3 is only ever the fallback branch.

**Recommendation — option B.** Bring the `room_availability` **table** forward,
read-only:

- the model, its migration, and its §7.6 indexes ship in Phase 3;
- catalogue reads it for indicative sellability and rate overrides;
- **nothing** in Phase 3 writes it, locks it, holds against it, or extends the
  calendar. `rooms_held`/`rooms_sold` stay at zero because only Phase 5 moves
  them, and the provider calendar upsert, the horizon job, the row-locked
  authoritative check and `inventory_hold` all remain Phase 5.

That is one table earlier than your boundary, and it buys a genuinely passing
TC-020, a truthful §14.2 rate resolution, and an "indicative" label that means
something rather than being a euphemism for "unknown".

Option A, if you would rather hold the line exactly: TC-021 passes in full;
TC-020 passes only its date/occupancy/pricing half; the availability assertion
ships as a written, `xfail(strict=True)` test tagged Phase 5 so it cannot be
forgotten and turns green the day the table lands. I will take A on request but
recommend B.

### Q3 — Presentment currency at display time contradicts §20 ⚠️ **decision needed**

You asked for prices in the tourist's presentment currency via `X-Currency`.
Two obstacles, both structural:

- §20.2: *"Mixed-currency catalogues are converted at **quote time, not at
  display time**, and the display shows the converted figure with a 'converted
  from' note."*
- §6.4 puts `fx_rate` in `finance`, which is L6. `catalogue` is L1. Catalogue
  cannot read the rate table, and import-linter will say so.

**Recommendation.** Serialise both, and never let the converted figure be
mistaken for the priced one:

```jsonc
"price": { "amount": "45.00", "currency": "USD" },   // authoritative, the listing's own
"display": {                                          // present only when X-Currency differs
  "amount": "117000", "currency": "TZS",
  "indicative": true, "converted_from": "USD",
  "rate_source": "fake", "rate_captured_at": "2026-08-19T00:00:00Z"
}
```

The conversion comes from a new `ExchangeRatePort` in `ports/` with a
deterministic fake, exactly as the routing and payment ports work. Phase 9
points the port at `finance.fx_rate` without a serializer change. `X-Currency`
resolution itself belongs to the `LocaleMiddleware` of §9.1 in `common`, so
every module gets it the same way.

This keeps §20.2 honest — quote time still owns the authoritative conversion —
and keeps the price the tourist will actually be charged visibly distinct from
the number they are browsing with.

### Q4 — §16.5 ranks on `agg.rating_avg`, which `catalogue` may not read ⚠️ **decision needed**

§6.4 gives `rating_aggregate` to `review`, which sits at L5 and depends on
`booking`. `catalogue` is L1. Neither may import the other, so the join in the
§16.5 expression cannot exist as written.

**Recommendation.** Denormalise `rating_avg NUMERIC(2,1) NULL` and
`rating_count INTEGER NOT NULL DEFAULT 0` onto `activity` and `accommodation`,
and have `review` publish a `RatingAggregateRecomputed` domain event on the
existing `apps.common.events` bus, which a catalogue-side subscriber applies.
Neither module imports the other; the bus is in the shared kernel, which is the
same resolution used for settings (ADR 0003), audit (Phase 2) and authorisation
(ADR 0005).

In Phase 3 the columns exist, stay at `NULL`/`0`, and the ranking honours
`NULLS LAST`. The subscriber is Phase 12's work; ordering tests here set the
columns directly, which is also the only way to test the ordering in isolation.

### Q5 — Map tiles are an unmade commercial decision ⚠️ **decision needed**

Four screens need a map. There is no map provider in Appendix D — D2 covers
*routing*, and while several routing vendors also sell tiles, the two are
separately licensed and separately priced.

**Recommendation.** Treat tiles exactly like every other vendor: MapLibre GL JS
(BSD, no vendor lock) against a **tile URL template held as a `system_setting`
row**, defaulting to OpenStreetMap raster tiles with the required attribution.
Swapping to MapTiler, Mapbox or whatever D2 resolves to is then a settings
change, not a deployment — which is also what §41.12 demands of everything else.

Please add this to the decisions register as **D9 — map tile provider**. It does
not block Phase 3; it blocks a production launch, because OSM's tile usage
policy does not permit commercial production traffic.

### Q6 — Lighthouse as an acceptance gate needs a CI job that does not exist

Asserting "Performance ≥ 90, SEO = 100, Accessibility ≥ 95, CLS < 0.1, LCP <
2.5 s" requires running Lighthouse against a real page with real seeded data.
That means one CI job that stands up Postgres, migrates, seeds, runs the API,
builds and starts the Next.js app, and runs `@lhci/cli` with those numbers as
hard assertions.

**Proceeding unless you object.** It is a heavy job (~4–6 minutes) and it is the
only way the thresholds stay true rather than being measured once by hand and
quietly rotting. It runs on pull requests touching `apps/web-tourist` and on
`main`.

### Q7 — Category chips must not be code

§24.7 lists chips: beaches, heritage, water sports, nature, culture. Those are
Zanzibar-shaped words and §4.2 prohibits them appearing in application code.

**Proceeding.** A `tag` table (slug, label, sort_order, is_active) supplies the
vocabulary, and `attraction.tags` / `activity.tags` are `text[]` columns with
GIN indexes — the array form is what §16.5's `tags && :interest_tags` operator
requires. The table is admin-managed; adding "diving" is a row.

### Q8 — The Add-to-Trip action on every detail screen belongs to Phase 4

§24.9, §24.10 and §24.13 all make "Add to Trip" the primary action, and trips do
not exist until Phase 4.

**Proceeding.** The control renders, disabled, with an explicit "Trip planning
opens in the next release" note, and Phase 4 flips it on. The alternative is to
omit it and redesign these screens next phase; the alternative I will not take
is a live button that routes to a dead page on the SEO surface.

### Q9 — §41.12 asks for a rendered page; there is no browser test harness yet

The destination-independence test says the new destination must "appear on the
public site, correctly rendered and in the sitemap". Playwright is in the §36.3
target layout but is not installed.

**Proceeding.** The test runs at two levels this phase: a backend integration
test that creates Arusha **only through the admin API**, then asserts it in
`/destinations`, `/attractions`, `/search` and the sitemap payload; and a
Next.js test that renders the destination route component against that exact
response. Playwright arrives with Phase 4, when there is a booking journey worth
driving end to end, and the §41.12 test is upgraded to a real browser then.

---

## 1. Domain surface — `apps/catalogue/domain/`

Pure Python. No Django, no ORM, no I/O, no clock reads, no settings lookups —
every threshold arrives as an argument. 95% coverage gate. Twelve modules:

### `visibility.py` — the single public-visibility predicate

```python
def is_publicly_visible(
    *, is_active: bool, deleted_at: datetime | None,
    launch_date: date | None, today: date,
) -> bool: ...

def visible_chain(*nodes: VisibilityNode) -> bool: ...   # parent inactive hides children
```

One predicate, one place. Pemba is `is_active=False`; a destination whose
`launch_date` is in the future is invisible without a deployment (§4.1); an
attraction inside an invisible destination is invisible regardless of its own
flag. `selectors.py` compiles the same rule into a join filter, and a test
asserts the two agree over a truth table rather than trusting that they do.

### `opening_hours.py` — §15.2

```python
@dataclass(frozen=True, slots=True)
class TimeRange:  opens: time; closes: time      # closes < opens means past midnight

@dataclass(frozen=True, slots=True)
class OpeningHours: week: Mapping[Weekday, tuple[TimeRange, ...]]
                    exceptions: Mapping[date, ClosureException]

def parse_opening_hours(raw: Mapping[str, object]) -> OpeningHours     # OpeningHoursError
def is_open_at(hours, instant: datetime, *, tz: ZoneInfo) -> bool
def week_view(hours, *, from_date: date, days: int, tz: ZoneInfo) -> tuple[DaySchedule, ...]
def next_open_at(hours, *, after: datetime, tz: ZoneInfo) -> datetime | None
```

`tz` is a required keyword everywhere. There is no signature in this module that
can be called without a timezone, which is how "evaluated in the destination's
timezone, not the server's" becomes structural. The tests run one attraction in
`Africa/Dar_es_Salaam` and the same JSON in `Pacific/Auckland` and
`America/Los_Angeles` and assert the answers differ at the hours they must.

### `ranking.py` — §16.5, exactly as written

```python
class SortOption(StrEnum): DEFAULT PRICE_ASC PRICE_DESC RATING DURATION DISTANCE

@dataclass(frozen=True, slots=True)
class RankInputs: destination_id; tags; feature_rank; rating_avg; rating_count; price; id

def rank_key(row: RankInputs, *, selected_destination_id, interest_tags) -> tuple[...]
def order_terms(sort: SortOption, *, selected_destination_id, interest_tags) -> tuple[OrderTerm, ...]
```

`rank_key` is the expression as a pure sort key, so the ordering is unit-tested
without a database; `order_terms` is the same expression as a declarative
description that `selectors.py` translates into ORM `order_by`. A test sorts a
fixture list with `rank_key` and asserts the database returns that exact
sequence — the two implementations of one rule are pinned to each other.

Pinned semantics, each a way this goes wrong:

| Field | Direction and reason |
|---|---|
| destination match | exact selected destination first, `DESC` on the boolean |
| tag overlap | boolean, not count — §16.5 says `&&` |
| `feature_rank` | **ascending** (1 outranks 100) |
| `rating_avg` | **descending**, `NULLS LAST` |
| `rating_count` | descending — breaks ties between equal averages |
| `price_per_person` | ascending |
| `id` | ascending, total-order tie-break |

A property test generates rows identical on every field except `id` and asserts
`rank_key` is injective, so the order is total and TC-902's byte-identity has
somewhere to stand.

### `pricing.py` — display arithmetic only

```python
def stay_nights(check_in: date, check_out: date, *, max_nights: int) -> int   # DateRangeError → TC-021
def stay_total(nightly: Sequence[Money]) -> Money
def nightly_average(total: Money, nights: int) -> Money
def per_person_total(unit: Money, pax: int) -> Money
```

`Money` is the Phase 1 value object: `Decimal`, `ROUND_HALF_UP`, currency
carried. `max_nights` comes from the existing `stay.max_nights` setting; the
domain never reads it. Rounding is applied once per line and once per aggregate
per §20.1, and `nightly_average` is a display figure derived *from* the total,
never a separate computation that could disagree with it.

### `occupancy.py` — BR-102

```python
def party_fits(*, max_adults, max_children, adults, children) -> bool
def rooms_required(*, max_adults, max_children, adults, children) -> int
```

### `cancellation.py` — §14.6

```python
@dataclass(frozen=True, slots=True)
class Tier: hours_before: int; refund_percent: Decimal

def validate_tiers(tiers) -> tuple[Tier, ...]     # strictly descending hours, 0..100, no dupes
def refund_percent_at(tiers, *, hours_before: Decimal) -> Decimal
```

Admins create policies in this phase, so the validation must exist now even
though §20.9's refund evaluation is Phase 8. FLEX_48H, MODERATE_7D, STRICT_14D
and NON_REFUNDABLE are seed rows, not classes.

### `requirements.py` — §16.4

Parses and validates the `requirements` JSONB into a typed structure (`min_age`,
`max_age`, `swimming_ability_required`, `medical_declarations`, `what_to_bring`,
`not_suitable_for`), rejecting unknown keys on admin write so a typo cannot
silently disable a booking guard in Phase 5.

### `schedules.py` — §16.2 weekday semantics

`WeekdayMask` bitmask with `occurs_on(mask, day)` and validity-window checks.
Materialisation is Phase 5; the mask semantics are needed now for admin
validation and for rendering a schedule.

### `search.py` — §7.6

```python
def normalise_query(raw: str, *, min_length: int, max_length: int) -> str   # QueryTooShortError
def to_websearch_query(q: str) -> str
class SearchKind(StrEnum): DESTINATION ATTRACTION ACTIVITY ACCOMMODATION
def merge_ranked(groups: Mapping[SearchKind, Sequence[Hit]]) -> tuple[Hit, ...]
```

`merge_ranked` orders by `rank DESC`, then a fixed `SearchKind` precedence, then
`id ASC` — total, so a unified search across four tables is as reproducible as
the single-table ranking.

### `geo.py`

```python
@dataclass(frozen=True, slots=True)
class Coordinates: lat: Decimal; lon: Decimal

def haversine_km(a: Coordinates, b: Coordinates) -> Decimal
```

Validation at construction (`-90..90`, `-180..180`). The distance-from-airport
chip of §24.8 does **not** use this directly — it calls `location`'s routing
port (the one dependency §6.4 grants `catalogue`, finally used), whose fake
returns haversine times a `route.road_factor` setting. Labelled indicative until
D2 lands.

### `slugs.py`

```python
def slugify_name(name: str) -> str                    # NFKD fold, ASCII, hyphenate, lowercase
def unique_slug(base: str, *, taken: Container[str]) -> str
```

Deterministic and market-neutral: "Chake Chake" and "Arusha" work identically,
and a non-ASCII name folds rather than failing.

### `media.py`

```python
def order_media(items: Sequence[MediaItem]) -> tuple[MediaItem, ...]   # primary, sort_order, id
def variant_url(*, base_url: str, key: str, width: int, fmt: str) -> str
```

`base_url` is the CDN root from settings, passed in. Content-hashed keys are
preserved so §35.7's long cache lifetimes hold.

### `hierarchy.py`

Country → region → destination invariants: ISO 3166-1 alpha-2 validation, IANA
timezone validation, ISO 4217 currency validation, and the gateway rules
(`gateway_type` and `gateway_code` are required iff `is_gateway`; `gateway_code`
is unique among gateways).

---

## 2. Data model

Eleven tables. Ten are `catalogue`'s; `activity_departure` turned out to belong
to `inventory` (ADR 0011), `provider_id` turned out not to be a foreign key
(ADR 0012), and `room_type` and `room_availability` turned out not to be v1 at
all (ADR 0013). Every one carries `public_id UUID`,
`created_at`, `updated_at` (trigger from §7.2), and `deleted_at` where soft
deletion applies.

| Table | Notes |
|---|---|
| `country` | `iso_code` CHAR(2) unique, `name`, `default_currency`, `default_timezone`, `is_active` |
| `region` | → country, `name`, `slug`, `is_active` |
| `destination` | §7.5.6 in full — `centroid geography(Point,4326)`, `is_gateway`, `gateway_type`, `gateway_code`, `timezone`, `default_currency`, `launch_date`, `feature_rank`, `is_active` |
| `tag` | `slug`, `label`, `sort_order`, `is_active` — the §24.7 chips |
| `attraction` | §15.1 — `coordinates`, `opening_hours` JSONB, `entrance_fee` + `fee_currency`, `visit_minutes`, `tags text[]`, `accessibility_notes`, `feature_rank`, `is_active` |
| `cancellation_policy` | `code`, `name`, `tiers` JSONB (ordered `{hours_before, refund_percent}`) |
| `accommodation` | §7.5.7 **as amended** — a location record: `destination_id`, `name`, `slug`, `property_type`, `coordinates`, `address_line`, `check_in_time`, `check_out_time`, `feature_rank`, `is_active`. No provider, no rate, no policy, no rating ([ADR 0013](adr/0013-accommodation-is-a-location-reference-in-v1.md)) |
| ~~`room_type`~~ | **v2 — [ADR 0013](adr/0013-accommodation-is-a-location-reference-in-v1.md).** Shipped in commit 19 and dropped by `catalogue/0006`. |
| ~~`room_availability`~~ | **v2 — [ADR 0013](adr/0013-accommodation-is-a-location-reference-in-v1.md).** Moved to `inventory` by [ADR 0011](adr/0011-inventory-owns-the-availability-tables.md), then dropped by `inventory/0002`. |
| `activity` | §16.1 — `provider_id`, `destination_id`, `attraction_id` NULL, `coordinates`, `meeting_point`, `duration_minutes`, `price_per_person`, `price_per_group`, `currency`, `min_pax`, `max_pax`, `requirements` JSONB, `inclusions`/`exclusions` JSONB, `tags text[]`, `cancellation_policy_id`, `booking_cutoff_hours`, `confirmation_mode`, `rating_avg`, `rating_count`, `feature_rank` |
| `activity_schedule` | §16.2 — `weekday_mask`, `start_time`, `capacity`, `valid_from`, `valid_to`. Model only. |
| ~~`activity_departure`~~ | **Moved to `inventory` — see [ADR 0011](adr/0011-inventory-owns-the-availability-tables.md)**, for the same reason. Model + read only; materialisation is Phase 5. |
| `media` | polymorphic `owner_type`/`owner_id`, `file_key`, `sort_order`, `is_primary`, `width`/`height`/`alt_text` |

**Indexes** per §7.6 plus what the ranking and search need:

```
GIST(destination.centroid), GIST(attraction.coordinates),
GIST(accommodation.coordinates), GIST(activity.coordinates)
GIN(attraction.tags), GIN(activity.tags)      — accommodation.amenities: v2
GIN(search_vector) on destination, attraction, activity, accommodation
UNIQUE(activity_departure.activity_id, departs_at)
INDEX(destination.region_id, is_active); UNIQUE(destination.slug)
partial UNIQUE(destination.gateway_code) WHERE is_gateway
INDEX(activity.destination_id, is_active, feature_rank)   -- the ranking's leading edge
partial UNIQUE(slug) WHERE deleted_at IS NULL             -- §7.7, as on user.email
```

`search_vector` is a **generated stored column** —
`GENERATED ALWAYS AS (to_tsvector('english', coalesce(name,'') || ' ' ||
coalesce(description,''))) STORED` — not a trigger. `to_tsvector(regconfig,text)`
is immutable, so Postgres maintains it; there is no application code that can
forget to update it, and no drift between a row and its index.

**Constraints:** money non-negative, `min_pax <= max_pax`, `capacity_held +
capacity_sold <= capacity_total`, `rooms_held + rooms_sold <= rooms_open`,
`star_rating BETWEEN 1 AND 5`, `feature_rank > 0`, `gateway_type`/`gateway_code`
non-null iff `is_gateway`, `valid_to >= valid_from`.

**GeoDjango** is enabled here: `django.contrib.gis` in `INSTALLED_APPS`, backend
`django.contrib.gis.db.backends.postgis`, and a `CreateExtension("postgis")`
migration operation ahead of the first geography column so a fresh database
bootstraps itself.

---

## 3. Authorisation

Thirteen new `Resource` members (`COUNTRY`, `REGION`, `DESTINATION`, `TAG`,
`ATTRACTION`, `CANCELLATION_POLICY`, `ACCOMMODATION`, `ROOM_TYPE`,
`ROOM_AVAILABILITY`, `ACTIVITY`, `ACTIVITY_SCHEDULE`, `ACTIVITY_DEPARTURE`,
`MEDIA`), and the `OWNERSHIP` map extended to stay total over `Role × Resource`
— the totality test will fail the build until every cell is filled, which is the
mechanism working as designed.

- `CATALOGUE_ADMIN`, `SUPER_ADMIN` → `GLOBAL` (this is §27.8)
- `SUPPORT_AGENT` → `GLOBAL_READ`
- `PROVIDER_OWNER`, `PROVIDER_STAFF` → `OWNED` on `activity` and its children,
  scoped by `provider_id`. No endpoint uses this path in Phase 3 — the provider
  portal is Phase 11 — but the rule is where §5.2 puts it, and leaving the cell
  at `DENY_ALL` would be a lie the totality test cannot catch. `accommodation`
  is **administered**, not provider-listed: a location record has no provider
  owner (ADR 0013), so it is scoped like `attraction`.
- Everyone else → `DENY_ALL`.

Public catalogue reads are **unauthenticated** (§9.3.2 shows `—` for auth), so
they are not an ownership question at all. They go on the `PUBLIC_BY_DESIGN`
allow-list in `test_authorisation_matrix.py`, each with its stated reason, and
the URL-conf enumeration test keeps them honest. Public querysets are filtered
by the `visibility.py` predicate, not by ownership — the same structural idea: an
inactive row is never loaded, so it cannot leak through a serializer.

---

## 4. API-02 — `apps/catalogue/views.py`

Twelve endpoints, all `GET`, all public, all cursor-paginated where they list.

```
GET /destinations                       ?region=&is_gateway=&cursor=
GET /destinations/{public_id}           + attraction/activity/accommodation counts
GET /attractions                        ?destination=&tags=&cursor=
GET /attractions/{public_id}            + opening hours for the coming week, fee, media
GET /activities                         ?destination=&date=&pax=&tags=&max_price=&sort=
GET /activities/{public_id}
GET /activities/{public_id}/departures  ?from=&to=&pax=
GET /accommodations                     ?destination=&q=&sort=      curated location records
GET /accommodations/{public_id}
                                        room-types: v2 (ADR 0013)
GET /search                             ?q=&kind=&destination=
GET /tags                               the §24.7 chip vocabulary
```

**Indicative labelling.** Every availability figure and every converted price
carries it in the payload, not in prose:

```jsonc
"availability": { "remaining": 6, "basis": "INDICATIVE", "authoritative_at": "QUOTE" }
```

`basis` is an enum with exactly one legal value in Phase 3. Phase 5 adds
`AUTHORITATIVE`, and no client has to change shape to receive it. The OpenAPI
description states the §14.3/BR-104 two-check rule verbatim so a consumer reading
the contract cannot conclude otherwise.

`GET /accommodations/{id}/room-types` is **v2** (ADR 0013). When it returns it
is the SRS's `roomtypes` path written correctly: §9.3.2's rendering splits it
across a line break and §24.13 writes it hyphenated.

`GET /accommodations` returns location records only — name, property type,
coordinates, address, check-in and check-out times. No price, no availability,
no `basis` field, because there is nothing indicative about a coordinate.

**No N+1.** Every list endpoint is written with `select_related` /
`prefetch_related` and pinned by `assertNumQueries` at a constant that does not
move with page size — the test parameterises over 1, 10 and 50 rows and asserts
the count is identical.

**NFR-P01 (400 ms p95).** Measured by a pytest-level query-count and timing
assertion per endpoint against the seeded dataset; LT-01's 100-concurrent load
test is Phase 14's harness, and I will say so rather than claim it here.

---

## 5. Admin catalogue CRUD — §27.8

Built in `apps/web-console` (React 19 + Vite + React Router, the app Phase 2 gave
sign-in to), against a new `POST/PATCH/DELETE /admin/catalogue/...` surface
guarded by `HasPermission.for_(Permission.CATALOGUE_MANAGE)` and the ownership
predicate.

Not Django admin. Django admin would be faster to stand up and would create a
second authorisation surface that bypasses §30.3's predicate entirely, plus a
second audit path — and §27.8 is specified as a console screen with a centroid
map pin. Every write is audited with before/after state per §41.13.

Screens: country, region, destination (with the map pin, gateway flags, timezone,
currency, launch date and activation toggle), attraction, tag, cancellation
policy, media upload, and administrative-correction views over activity and
accommodation.

---

## 6. Seed data — Appendix C

`database/seeds/` as YAML, loaded by `python manage.py seed_catalogue` behind
`make seed`. Idempotent — keyed by natural key, safe to re-run, and re-running is
part of the test.

1 country (Tanzania, TZS, `Africa/Dar_es_Salaam`) · 5 regions · **10 destinations
with Pemba `is_active=false`** · ZNZ as the one gateway · ~25 attractions · 4
cancellation policies · 4 vehicle classes. Accommodation and activities are
**not** seeded — Appendix C is explicit that providers own those, and the admin
console creates the handful the tests need.

The seed file contains no Zanzibar-specific *logic*, only rows. A test asserts
the loader is data-driven by loading a second, synthetic country through the same
code path.

---

## 7. Tourist web — page designs

Every page below is a React Server Component with `generateMetadata`, JSON-LD,
`next/image` with explicit dimensions, and **built** loading, empty and error
states. `revalidate` is set so a new destination appears without a deployment;
`dynamicParams` is on, so a slug that did not exist at build time renders.

### 7.1 Explore — `/explore` (§24.7)

```
┌──────────────────────────────────────────────────────────────┐
│  Explore Zanzibar                                            │
│  ┌────────────────────────────────────┐  ┌───────┬────────┐  │
│  │ 🔍 Search destinations, activities │  │ List  │  Map   │  │
│  └────────────────────────────────────┘  └───────┴────────┘  │
│  ( beaches )( heritage )( water sports )( nature )( culture )│ ← from `tag`
├──────────────────────────────────────────────────────────────┤
│  Destinations                                                │
│  ┌────────────┐┌────────────┐┌────────────┐┌────────────┐    │
│  │  [image]   ││  [image]   ││  [image]   ││  [image]   │    │
│  │ Stone Town ││  Nungwi    ││   Paje     ││  Matemwe   │    │
│  │ 7 km ZNZ   ││ 57 km ZNZ  ││ 48 km ZNZ  ││ 55 km ZNZ  │    │
│  └────────────┘└────────────┘└────────────┘└────────────┘    │
│  Activities                          [ sorted: recommended ] │
│  ┌────────────┐┌────────────┐┌────────────┐┌────────────┐    │
│  │ Mnemba …   ││ Spice Farm ││ Jozani …   ││ Sunset Dhow│    │
│  │ 4h · $45pp ││ 3h · $30pp ││ 5h · $40pp ││ 2h · $25pp │    │
│  └────────────┘└────────────┘└────────────┘└────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

Search debounced at 350 ms, minimum two characters (§24.7). The map toggle
lazy-loads MapLibre into a fixed-aspect container — the container reserves its box
in the server-rendered HTML so the map mounting costs no CLS. Empty state offers
to clear filters; error state retries per-section, so a failed activity fetch does
not blank the destinations.

### 7.2 Destination — `/destinations/[slug]` (§24.8) · the SEO page

```
┌──────────────────────────────────────────────────────────────┐
│  [ hero image — priority, LCP element, explicit w/h ]         │
│  Nungwi                              ( 57 km from ZNZ ✈ )    │
│  Zanzibar North · Tanzania · UTC+3 · prices in USD           │
├──────────────────────────────────────────────────────────────┤
│  Nungwi sits at the northern tip of Unguja, where the tide …  │
│                                                              │
│  ┌──────────────────────────┐   Getting here                 │
│  │        [ map ]           │   57 km from Abeid Amani       │
│  │     ● destination        │   Karume International (ZNZ)   │
│  │     ✈ gateway            │   ~1 h 20 min by road          │
│  └──────────────────────────┘   Indicative until booked      │
├──────────────────────────────────────────────────────────────┤
│  [ Attractions (6) ] [ Activities (14) ] [ Stays (9) ]       │
│  ┌────────────┐┌────────────┐┌────────────┐                  │
│  │ …cards, tab-scoped, each tab loading independently…        │
└──────────────────────────────────────────────────────────────┘
```

Tabs load independently (§24.8) — one failure does not blank the screen. The hero
is the LCP element and is `priority`, preloaded, with `sizes` set; nothing above
the fold waits on client-side data.

`generateMetadata` → title `Nungwi — beaches, activities and places to stay`,
description from `summary`, canonical `/destinations/nungwi`, OG image from the
primary media row. JSON-LD `TouristDestination` with `geo`, `containedInPlace`
(region → country) and `touristType`.

### 7.3 Attraction — `/attractions/[slug]` (§24.9)

```
┌──────────────────────────────────────────────────────────────┐
│  [ gallery — 1 large + 4 thumbnails ]                         │
│  Jozani Forest                       Nungwi › Zanzibar North │
├───────────────────────────────┬──────────────────────────────┤
│  About                        │  Opening hours               │
│  Jozani is the last …         │  Today   09:00 – 18:00  Open │
│                               │  Wed     09:00 – 18:00       │
│  Recommended visit  90 min    │  Thu     09:00 – 18:00       │
│  Tags  nature · wildlife      │  Fri     09:00–12:00, 14:00– │
│                               │  Sat     09:00 – 18:00       │
│  ┌─────────────────────────┐  │  Sun     Closed              │
│  │        [ map ]          │  │  Mon     Closed — public hol.│
│  └─────────────────────────┘  │  Times shown in Nungwi time  │
│                               │  (UTC+3)                     │
├───────────────────────────────┴──────────────────────────────┤
│  ⓘ Entrance fee $10 per person — paid on site, not included  │
│    in your trip total.                                       │
├──────────────────────────────────────────────────────────────┤
│  Related activities                     [ Add to trip ⃠ ]     │
│  ┌────────────┐┌────────────┐            soon                │
└──────────────────────────────────────────────────────────────┘
```

The week table is the visible proof of the timezone rule: the "Today" row is
computed in the destination's zone, and the label states which zone. The
entrance-fee note is §15.3's wording, not a paraphrase. JSON-LD
`TouristAttraction` with `openingHoursSpecification` derived from the same parsed
structure the table renders — one source, two outputs.

### 7.4 Activity — `/activities/[slug]` (§24.10)

```
┌──────────────────────────────────────────────────────────────┐
│  [ gallery ]                                                 │
│  Mnemba Atoll Snorkelling            ★ New on the platform   │
│  by Blue Horizon Tours                                       │
├───────────────────────────────┬──────────────────────────────┤
│  4 hours · 2–12 people        │  From  $45  per person       │
│  Meeting point: Matemwe beach │  TZS 117,000 indicative      │
│  Confirms instantly           │                              │
│                               │  ◀  August 2027  ▶           │
│  Includes                     │  M  T  W  T  F  S  S         │
│   ✓ Boat, guide, equipment    │           1  2  3  4         │
│   ✓ Bottled water             │   5  6  7  8  9 10 11        │
│  Excludes                     │  12 13 14 15 16 17 18        │
│   ✕ Marine park fee ($5)      │                              │
│                               │  Thu 12 Aug  08:30   6 left  │
│  Requirements                 │  Fri 13 Aug  08:30   Full ⃠  │
│   • Minimum age 8             │  Sat 14 Aug  08:30  Cancelled│
│   • Must be able to swim      │                              │
│   • Bring swimwear, towel     │  Pax  [ − ] 2 [ + ]          │
│                               │  [ Add to trip ⃠ ] soon      │
│  Cancellation  FLEX_48H       │                              │
│   Full refund up to 48 h …    │  Seats shown are indicative  │
└───────────────────────────────┴──────────────────────────────┘
```

Empty departures shows the next available date; sold-out renders disabled with
"Full"; the departures panel retries on its own (§24.10). JSON-LD `Product` with
an `Offer` carrying `price`, `priceCurrency` and `availability`.

### 7.5 Where are you staying — `/trip/[id]/stay` (§24.11 as amended)

```
┌──────────────────────────────────────────────────────────────┐
│  Where are you staying?          In [12 Aug]  Out [16 Aug]   │
├───────────────┬──────────────────────────────────────────────┤
│               │  Search Nungwi properties…                   │
│    [ map ]    │  ┌──────────────────────────────────────────┐│
│      📍       │  │ Kilindi Zanzibar          Resort · Nungwi││
│               │  │ Kendwa Rocks              Hotel  · Kendwa││
│  confirm pin  │  └──────────────────────────────────────────┘│
│               │  Can't find it?  [ Enter any hotel or address ]│
│               │  ☐ I haven't booked anywhere yet             │
└───────────────┴──────────────────────────────────────────────┘
```

One screen, no prices, no availability, no room types. Two ways in:

- **Curated property** — pick from the destination's seeded accommodation
  records. The coordinate is exact, so the transfer either side quotes to the
  metre.
- **Free entry** — any hotel name or address, resolved through the §13.2
  geocoding path and shown as a pin the tourist confirms. **An unconfirmed
  geocode is never silently persisted**: no confirmation, no anchor.

"I haven't booked anywhere yet" skips the anchor and leaves VR-16's warning
standing, which is honest rather than obstructive — a trip still quotes with an
uncovered night, it just cannot plan transfers around it.

Check-out before check-in is a client-side block *and* a 422
`INVALID_DATE_RANGE` from the server, bounded by `stay.max_nights`; TC-021 is
asserted at the API, not at the form. `domain.pricing.stay_nights` is what both
sides use.

Where a Booking.com affiliate deep link is configured (§38.2, Appendix D10) a
property card may offer "book this stay". It leaves the Platform, it is never
part of the basket, and nothing in the booking, pricing or itinerary path
depends on it.

### ~~7.6 Accommodation detail — `/stays/[slug]`~~ — v2 (§24.12, §24.13)

Deferred with the subsystem (ADR 0013). §24.11 in its amended form is the whole
of the accommodation experience in v1. Numbering retained so the commit list and
the §24 cross-references keep resolving.

### 7.7 Cross-cutting web deliverables

- `app/sitemap.ts` — every active, launched, non-deleted destination, attraction,
  activity and accommodation, from the API, with `revalidate`. A new destination
  appears on the next revalidation with no deployment (§41.12).
- `app/robots.ts` — allows the catalogue, disallows `/(auth)` and any future
  authenticated area, and points at the sitemap.
- A `Money` display component that formats by locale via `Intl.NumberFormat` and
  renders the "converted from" note of Q3 when the figure is indicative.
- A `LocalTime` component that formats in the destination's IANA zone and
  **always renders the zone label** — there is no way to call it without one.
- Skeletons sized to the content they replace, which is most of the CLS budget.

---

## 8. Tests and how each acceptance criterion is discharged

| Criterion | Mechanism |
|---|---|
| Ten destinations browsable, searchable, filterable | Integration test over the seeded set per endpoint |
| **TC-902 determinism** | Same request 50×, assert byte-identical serialised bodies; plus the `rank_key` injectivity property test |
| ~~**TC-020**~~ | Void in v1 (ADR 0013); amended in the SRS, not deleted, and returns with v2 |
| **Stay anchoring** | A curated property and a free-entry address each produce a STAY item with coordinates and dates and no price; an unconfirmed geocode produces none (§13.2); §41.4 as amended |
| **TC-021** | 422 `INVALID_DATE_RANGE`, asserted at the API and in the domain |
| **§41.12 Arusha** | Backend test creating country/region/destination/attraction **only through the admin API**, then asserting presence in `/destinations`, `/attractions`, `/search` and the sitemap payload; plus a Next.js render test. Written **first**, in the first third of the phase, per your instruction |
| Timezone | The same opening-hours JSON evaluated in three zones, asserting different answers |
| `feature_rank` ASC, rating DESC | One test per direction, each failing if inverted |
| Soft-deleted and inactive absent | Parameterised over every public endpoint **and** the sitemap; asserts against the explicit visibility filter, not the default manager |
| **Pemba** | Named test: Pemba is absent from `/destinations`, `/search`, the sitemap and its own detail URL (404), and present in the admin list |
| No N+1 | `assertNumQueries` parameterised over 1/10/50 rows, constant count |
| Lighthouse | `@lhci/cli` CI job with the five thresholds as hard assertions |
| Module boundaries | import-linter; `catalogue → location` is now exercised for the first time |
| Coverage | 80% overall, 95% on `apps/catalogue/domain/*` |

---

## 9. Commit sequence

Roughly 34 commits, each one logical change, each pushed.

1. ADR 0009 — GeoDjango's real toolchain cost (Q1); ADR 0004 marked superseded in part
2. CI: GDAL/GEOS/PROJ in the three backend jobs
3. Containerised backend test target; README setup rewritten
4. Enable `django.contrib.gis` + the PostGIS backend + `CreateExtension`
5. Domain: `visibility`
6. Domain: `hierarchy`
7. Domain: `slugs`
8. Domain: `opening_hours`
9. Domain: `geo`
10. Domain: `media`
11. Domain: `ranking`
12. Domain: `search`
13. Domain: `pricing` + `occupancy`
14. Domain: `cancellation`
15. Domain: `requirements` + `schedules`
16. `ExchangeRatePort` + fake (Q3); `LocaleMiddleware` currency resolution
17. Models and migrations: geography hierarchy
18. Models and migrations: attraction, tag
19. Models and migrations: accommodation, room_type, room_availability
    — ~~room_type~~ and ~~room_availability~~ removed again in 22a/22b (ADR 0013)
20. Models and migrations: activity, schedule, departure, media, cancellation policy
21. `search_vector` generated columns and the GIN indexes
22. Authorisation resources and the extended `OWNERSHIP` map
22a. ADR 0013 + SRS v1.2; `room_availability` and `room_type` leave the schema;
     accommodation reduced to a location record; domain functions marked v2
23. Repositories and selectors — the visibility filter and the ranking translation
24. **§41.12 Arusha acceptance test** (fails until 25–27 land — deliberately, and early)
25. Admin catalogue write API + audit
26. Public read API-02
27. `/search` and `/tags`
28. Seed loader, seed data, `make seed`
29. OpenAPI regeneration + TypeScript contract types
30. Web: shell and shared components (Money, LocalTime, Map, gallery)
31. Web: Explore + Destination
32. Web: Attraction + Activity
33. Web: "Where are you staying" (§24.11 as amended) — curated list, free
    entry with a confirmed map pin, check-in and check-out. ~~Stays detail and
    room selection~~ are v2 (§24.12, §24.13)
34. `sitemap.ts`, `robots.ts`, JSON-LD, Lighthouse CI job

---

## 10. Notes back to you

- **D9 (map tiles)** — **resolved and registered.** Appendix D gains D9 in SRS
  v1.3; the decision is [ADR 0016](adr/0016-map-tiles-are-a-configured-url-behind-maplibre.md).
- **D3** covers cloud and region, and key management follows from it. Correcting
  myself: I labelled key management "D8" in the Phase 2 report, but Appendix D
  already has a D8 (support operating hours). Your framing is the right one — it
  is not a new decision, it is a consequence of D3.
- **D1** and **D2** unchanged. D2 still blocks Phase 4 completion, and the
  distance-from-airport chip in §7.2 above is labelled indicative until it lands.

---

## 11. What the build found

### Defects the tests caught rather than review

1. **The §41.12 acceptance test could never have passed the way it was
   written.** `_administrator()` from commit 24 registered and granted a role,
   then called `authenticate` — but §30.2 makes TOTP mandatory for
   `CATALOGUE_ADMIN`, so it raised `MfaRequiredError` at login. The test was
   failing before the POST it existed to assert against, so its `xfail` was
   reporting the wrong reason. Fixed by enrolling and confirming TOTP; the
   strict marker was kept through the change.

2. **Moving the `Point` construction out of the serializer dropped a 422.**
   `to_orm_fields` became the single place degrees become geometry — right,
   because two conversions are two chances to disagree about which of latitude
   and longitude is a `Point`'s `x` — but the `ValueError → 422` translation did
   not come with it, so an out-of-range latitude became a 500. Caught by
   `test_an_out_of_range_coordinate_is_refused`, which already existed.

3. **BR-127 was implemented nowhere.** The read API had served `rating_avg`
   unconditionally since commit 26, so any client could render `5.0 ★` for an
   activity with one review — with four screens about to be built against that
   payload. Found by writing [ADR 0015](adr/0015-ratings-are-a-projection-into-catalogue.md),
   not by a test, because no test asserted a rule nobody had implemented.

4. **`cancellation_policy` shipped as a model in commit 20 and stopped there.**
   No entity, no write API, no audit path, no seed file — while Appendix C
   commits to four and §5 above names a console screen for it. The plan did not
   defer it; commits 25 and 28 missed it. Found while checking that Appendix C's
   *later-phase* rows had been left alone.

   What makes it a good outcome rather than only a bug is that the phase's own
   guards found it and not a reviewer.
   `test_every_entity_with_a_create_route_writes_an_entry` compares its request
   bodies against `ENTITIES` and failed the moment an eighth appeared; the §37.2
   matrix then refused the new create route until it was named in
   `NO_ROWS_EXPOSED`, where commit 25's guard re-proved it create-shaped. The
   comment there had predicted exactly this — *"adding an eighth curated table is
   a line in this file that a reviewer sees"*.

### Controls that were green without asserting anything

5. **§6.5 rule 1 was unsatisfiable as encoded.** The fourteen `private-*`
   import-linter contracts used reachability semantics, so
   `administration → catalogue.services → catalogue.models` was a violation —
   produced by doing exactly what the rule *requires*. It survived all of Phases
   1 and 2 and twenty-eight commits of Phase 3 because no code had ever made a
   cross-module `services.py` call. The contracts were green the whole time and
   proved nothing about the property they exist to protect; the first attempt to
   comply broke them.
   [ADR 0014](adr/0014-module-privacy-contracts-check-direct-imports.md).

6. **`v1:identity:device-detail` had been wrongly exempt since Phase 2.** It sat
   on `NO_ROWS_EXPOSED` with a `<uuid:public_id>` path parameter and an exemption
   reason that did not apply to it. Found by the self-policing guard on its first
   run — the list was already rotting before anyone was tempted to abuse it.

7. **`catalogue/0007` had to write a vacuously-passing default.** Adding NOT NULL
   bounding-box columns to an existing table needs a value for the rows already
   there, and there is no correct one, so it writes the whole-world box — which
   passes every bounds check. Closed by
   `test_the_shipped_data_would_notice_a_swap`, which transposes every coordinate
   in the seed set and requires its country to reject it; the world box fails
   that for every row at once. Mutation-tested in both directions.

### Known-unproven

8. **The §6.5 rule 5 architecture test reads annotations, not values.**
   `test_services_never_return_orm_instances` resolves each public service
   function's return annotation and fails on a `Model` or `QuerySet`. A function
   annotated `-> Any`, left unannotated, or returning a dataclass that holds a
   `Model` in a field passes it. Since ADR 0014 narrowed the privacy contracts to
   direct imports, this test is now the **only** thing standing between us and an
   ORM instance crossing a module seam, and it is weaker than its name suggests.
   Two rules follow that nothing currently enforces: a service function must
   carry a real return annotation, and a DTO must not hold an ORM instance.
   Worth enforcing the next time this area is touched.

9. **A country's bounding box only catches a transposed pair where that
   country's latitude and longitude ranges do not overlap.** True for Tanzania
   (−11.7..−1.0 against 29.3..40.4) and for most countries, but a property of the
   geography rather than a guarantee of the technique. The check is a cheap net
   with a known hole, and the shipped-data test asserts the net holds for the
   rows that actually ship rather than assuming it.

15. **Six links in the shipped web client pointed at routes that did not
    exist, and one of them broke a consent flow.** `/destinations`,
    `/attractions` and `/activities` were written into the header with the
    shell in commit 30; commits 31 and 32 built the *detail* routes and no
    index route was ever added, so three of the four navigation links were
    404s for three commits. Broadening the check past the shell found
    `/help`, `/terms` and `/privacy` in the footer, `/verify-email` on the
    sign-in screen, and `/terms` again on registration.

    None of it was visible to any existing control. The header renders, its
    tests pass, every page it appears on builds, and `next build` lists the
    routes that exist without checking what links to them. This is the
    "green without asserting anything" class again, in a new place: the whole
    of navigation had no assertion behind it.

    `navigation.test.ts` now derives the route table from the App Router
    directory and fails on any literal `href="/…"` under `src` that resolves
    to nothing. Derived rather than hand-kept, because a hand-kept list would
    need updating in the same commit that adds a page — the discipline that
    had already failed.

16. **Registration requires consent to a document that does not exist.** The
    checkbox is required to submit and its "terms of use" link was a 404, so
    a user was agreeing to something they could not open. The dead link is
    removed rather than repointed at a stub, because a placeholder under that
    heading is a liability rather than a placeholder. **This is a release
    blocker, not a bug**: registration must not reach production without the
    document, and the same holds for the privacy notice removed from the
    footer. Neither is code, so neither can be closed here.

17. **The web client had no route to email verification at all.** `/auth/verify-email`
    exists on the API and `lib/auth.ts` has always called it, but no page ever
    consumed the token, so the link in the verification email had nowhere to be
    spent and an unverified account could not be recovered from the web. Built
    in the same commit. Related and still open: the sign-in screen offered to
    *resend* the verification email, and the API exposes no resend endpoint —
    the wording now says what actually works, but §24.4's resend affordance
    needs an endpoint before it can exist.

### Corrections to this plan

10. **Q4's recommendation was wrong against the SRS.** It proposed
    `rating_avg NUMERIC(2,1) NULL`. SRS §7.5's column table specifies
    `NUMERIC(3,2) NOT NULL DEFAULT 0.00`, which is what commit 20 shipped. ADR
    0015 adopts the SRS and says so rather than ratifying the plan.

11. **Seed data restates `timezone` and `default_currency` on all ten
    destinations**, identically to the country's defaults. A loader-only
    inheritance rule would have been a second write path behaving differently
    from the console, and §4.2 is explicit that these are *defaults a destination
    may override*. Repetition is the correct cost.

12. **`xfail(strict=True)` on the §41.12 test fired twice**, which is the whole
    reason for the strictness. At commit 26 the deactivation half XPASSed and
    forced a split; at commit 27 the opening flow XPASSed and the marker was
    removed. Under a plain `xfail` this acceptance criterion would have read as
    unmet while actually passing since commit 26.

13. **Two test assertions were corrected rather than the code.** DRF reads an
    empty query parameter on a non-required field as absent, so `?cursor=` is
    correctly the first page, not a 422; and `normalise_query` truncates at
    `search.max_length` rather than refusing, which closes the same DoS hole —
    nothing past the ceiling reaches PostgreSQL — and is kinder to someone
    pasting a paragraph.

14. **§7.2's "57 km from ZNZ" chip was specified here and not built.** The
    mockup in this document shows a distance on the Explore cards and on the
    Destination page, and the JSON-LD block was to carry `geo` alongside it.
    The figure does not exist yet: D2 is unresolved, the routing adapter is a
    fake, and every distance the platform can currently produce is a haversine
    estimate that §12.6 requires be rendered with an explicit *"approximate"*
    label.

    On a page that is fine — a label can be shown. In **structured data it is
    not**: `schema.org` has nowhere to carry "this number is approximate", so
    publishing it would send a fabricated figure to search engines as fact,
    about the most-indexed page in the product, to a tourist deciding where to
    stay. `domain.geo.Distance.may_be_stated_as_fact` already encodes exactly
    this rule — it returns `True` only for a `MEASURED` distance — and commit
    31 is the first place it had to be obeyed rather than merely tested.

    So the JSON-LD carries only measured facts: `geo` from the row, and the
    `containedInPlace` chain. The chip returns when D2 lands and distances
    become `MEASURED`, and not before. **A plan is not evidence that the data
    supports what it specifies**, which is the general form worth keeping.


18. **§24.11 could not be built as specified, and the reason was larger than
    the geocoder.** The screen ends at `POST /trips/{id}/items` with
    `item_type` STAY. `apps/api/apps/trip/` is still the Phase 1 skeleton —
    no `trip`, no `itinerary`, no `itinerary_item` — so **nothing on the
    screen can be saved by any path**, curated included. `apps/api/apps/location/`
    is a skeleton too: no `RoutingPort`, no geocoder, not even a fake.

    So commit 33 ships the selection surface with a disabled submit and its
    reason stated, matching the "Add to trip" treatment already on the
    Activity page for the same missing module. Free entry captures the name
    and **no coordinate**. The tempting alternative — place a pin and let the
    tourist confirm it — is the one thing §13.2 forbids, and the confirmation
    step makes it worse rather than better: it launders a coordinate the
    Platform invented into one the tourist appears to have vouched for, after
    which a transfer quotes from it to the metre. §24.11's
    "geocode-failed → drop the pin yourself" is not built either; a
    hand-dropped pin is legitimate human confirmation, but building the
    fallback for a path whose primary half does not exist inverts the screen.

    The route is `/stays?destination=…` rather than the plan's
    `/trip/[id]/stay`, for the same reason: an `[id]` segment with nothing
    behind it is the same invention as the pin. It moves when the trip module
    lands.

19. **`GET /config` grew its first threshold, under a written rule.** §24.11
    asks the screen to refuse an over-long stay before submitting it, so the
    client needs `stay.max_nights` — and the alternatives were to hardcode 30
    in the front end, which is the NFR-M07 violation `public_config` exists to
    prevent and which no allow-list would have caught, or to skip the
    validation the SRS specifies.

    Admitting a threshold on judgement makes the next one easier, so the test
    for admission is now in the module rather than left as a precedent: a
    screen the SRS specifies cannot do its job without the value, **and**
    knowing it in advance confers no advantage. A §11.6 dispatch weight fails
    the second; a §30.14 fraud threshold fails it harder. `test_the_public_set_is_exactly_this`
    pins the five names literally so a sixth is a deliberate edit to a test
    whose docstring is that rule.

    Found while doing it: the response schema is hand-declared on the view and
    could drift from the allow-list in either direction, both silently — a
    field served but undocumented never reaches `@pumba/contracts`, and one
    documented but not served becomes a TypeScript property that is `undefined`
    at runtime, which typechecks. Now asserted against the generated schema.
