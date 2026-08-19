# ADR 0004 — PostGIS is provisioned now, GeoDjango is enabled in Phase 3

**Status:** Accepted, superseded in part by [ADR 0009](0009-geodjango-requires-a-containerised-test-run.md)
**Date:** 2026-08-18 · **Phase:** 1

## Context

The brief lists "PostgreSQL 16 + PostGIS 3.4 (GeoDjango enabled)" under the
Phase 1 stack. SRS §7.2 requires `geography(Point, 4326)` for all coordinates,
and §7.1 justifies PostGIS as part of the database decision.

But Phase 1 has no geospatial columns. The first are `destination.coordinates`
and `attraction.coordinates` in Phase 3, and `driver_location` in Phase 10.
Adding `django.contrib.gis` to `INSTALLED_APPS` now would mean every developer
and every CI job carrying a GDAL runtime dependency to support zero geometry
fields — and the brief also says not to scaffold for later phases.

## Decision

- The Compose stack runs `postgis/postgis:16-3.4` from day one, so the
  extension is present and the database image never has to change.
- The API container image installs `gdal-bin`, `libgdal-dev`, `libgeos-dev`
  and `libproj-dev` from day one, so the runtime is ready.
- `django.contrib.gis` is **not** in `INSTALLED_APPS`, and the database backend
  is `django.db.backends.postgresql` rather than the PostGIS backend.

Phase 3 enables GeoDjango by changing two settings lines. No infrastructure,
image or Compose change is required.

> **Corrected in Phase 3.** The settings change is indeed two lines and the
> image and database needed no change — but the test suite does not run in
> that image. Neither the developer host nor the CI runner had GDAL, so
> enabling GeoDjango also required an `apt-get` step in three CI jobs and
> moving the backend suite into the `api` container. See
> [ADR 0009](0009-geodjango-requires-a-containerised-test-run.md).

## Consequences

Phase 1 runs on any machine with Docker without a host GDAL install, and CI
needs no geospatial system packages. The deviation is narrow and reversible,
and the parts that are expensive to change later — the database image and the
container base — are already correct.

The risk is that "GeoDjango enabled" is ticked off as done when it is not. It
is recorded here and in the Phase 1 report as an explicit deviation, and Phase 3
cannot start its first model without hitting it.

That last paragraph held: Phase 3 could not write its first model without
hitting it. What it did not anticipate is *where* the cost landed — see ADR
0009.
