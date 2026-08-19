# ADR 0009 — Enabling GeoDjango moves the backend test run into the container

**Status:** Accepted · **Date:** 2026-08-19 · **Phase:** 3
**Supersedes in part:** [ADR 0004](0004-geodjango-deferred-to-phase-3.md)

## Context

ADR 0004 deferred `django.contrib.gis` to Phase 3 and closed with:

> Phase 3 enables GeoDjango by changing two settings lines. No infrastructure,
> image or Compose change is required.

That was true of the **container**. `api.Dockerfile` has carried `gdal-bin`,
`libgdal-dev`, `libgeos-dev` and `libproj-dev` since Phase 1, and the database
has been `postgis/postgis:16-3.4` from the first commit. Both were correct and
both are unchanged by this ADR.

It was wrong about the two places the test suite actually runs, neither of which
is that container:

```
apps/api $ uv run python -c "from django.contrib.gis.gdal import gdal_version"
ImproperlyConfigured: Could not find the GDAL library (tried "gdal308" …)
```

* **The developer host.** `make test` runs `uv run pytest` directly. GeoDjango
  binds native libraries through `ctypes` at import time, so the moment
  `django.contrib.gis` enters `INSTALLED_APPS` every backend test fails at
  collection — including the several hundred with no geometry anywhere near
  them. The primary development host here is Windows, which has no system
  package manager path to GDAL, GEOS and PROJ.
* **CI.** The `backend-lint`, `module-boundaries` and `backend-test` jobs run
  `uv sync` on a bare `ubuntu-latest` runner. They do not build or use
  `api.Dockerfile`. The runner image carries no geospatial libraries either.

There is no pure-Python remedy. `django.contrib.gis` is a binding, not an
implementation.

## Decision

1. **CI installs the libraries.** One `apt-get install gdal-bin libgdal-dev
   libgeos-dev libproj-dev` step in each of the three backend jobs. This also
   makes the runner match `api.Dockerfile`, which it previously did not.

2. **`make test` runs the backend suite inside the `api` container.**
   `docker compose run --rm api pytest`. The container already carries the
   libraries by ADR 0004's own design, so this needs no image change.

3. **`make test-host` remains** for anyone whose host does have GDAL, and for
   the fast inner loop on pure-domain tests that touch no geometry.

## Consequences

The second point is the better outcome independent of GDAL, and is the reason
this is a decision rather than a workaround: the suite now runs against the same
Python, the same system libraries and the same PostGIS version as the deployed
runtime. That removes a whole class of "passes locally, fails deployed" —
locale, timezone database, `libpq` version and PostGIS function availability
were all previously unpinned between the developer host and production.

The cost is a slower cold start (the container must be built once) and one more
moving part between a developer and a red test. `make test-host` keeps the fast
path available for the domain layer, which is the layer that changes most often
and the layer with no native dependency at all.

Setup instructions stay platform-neutral. The alternative — documenting an
OSGeo4W install and a per-platform `GDAL_LIBRARY_PATH` — would have made §37.1's
"a new engineer runs the stack locally with one command" false on the first
sentence.

## What ADR 0004 got right, and what it did not

Right: provisioning PostGIS and the image libraries from day one. Those are the
expensive things to change late, and neither had to change.

Wrong: measuring the cost of enabling GeoDjango by counting settings lines. The
settings change is indeed two lines. The toolchain change is a CI step and a
test-runner move, and it was invisible from where ADR 0004 was written because
Phase 1 had no geometry column to force the import.
