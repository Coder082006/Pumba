# ADR 0010 — Image dependencies come only from registries reachable from the development environment

**Status:** Accepted · **Date:** 2026-08-19 · **Phase:** 3

## Context

`api.Dockerfile` obtained `uv` with

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
```

which is the pattern Astral documents and is a perfectly good one. It cannot be
built here. `ghcr.io` does not resolve from the development environment:

```
failed to solve: ghcr.io/astral-sh/uv:0.5.11: failed to fetch anonymous token:
  Get "https://ghcr.io/token?scope=repository%3Aastral-sh%2Fuv%3Apull&service=ghcr.io":
  dial tcp: lookup ghcr.io: … the local server did not receive a response from
  an authoritative server
```

Three consecutive builds failed the same way, while `docker.io` and `pypi.org`
resolved and served in the same session. This is a standing local network
condition, not a transient outage.

GitHub Actions *can* reach `ghcr.io`, so leaving the reference in place would
have produced a green pipeline against an image nobody on this machine could
build. That is a worse failure than a red one: the pipeline would assert
correctness of an artefact that could not be reproduced or debugged where the
work happens, and the first person to hit it would have no reason to suspect
the registry.

This mattered immediately rather than eventually because [ADR
0009](0009-geodjango-requires-a-containerised-test-run.md) had just made that
image the place the backend test suite runs. An unbuildable image meant an
unrunnable suite.

## Decision

**Every dependency pulled during an image build must come from a registry
verified reachable from the development environment.** Verified means a build
was actually run, not that the host is expected to be reachable.

Currently reachable and approved:

| Registry | Used for |
|---|---|
| `docker.io` | base images (`python`, `postgis/postgis`, `redis`, `node`) |
| `pypi.org` | Python packages, including build tooling |
| `registry.npmjs.org` | Node packages |

Currently **not** reachable, and not to be used:

| Registry | Note |
|---|---|
| `ghcr.io` | Does not resolve here. Recheck before adopting; do not adopt on the assumption it has been fixed. |

Concretely, `uv` is now installed from PyPI at an exact pinned version:

```dockerfile
ARG UV_VERSION=0.5.11
RUN pip install --no-cache-dir --root-user-action=ignore "uv==${UV_VERSION}"
```

The fix is in the Dockerfile, deliberately, and not in CI configuration or a
registry mirror. A build that succeeds only on someone else's network is not a
build this team can trust.

## Consequences

The supply chain gets one registry shorter rather than one longer. PyPI was
already the source of every other Python dependency in this image, so `uv` now
arrives the same way as `django` and `psycopg` — one trust root for Python
packages instead of two.

What is given up is the small integrity advantage of the `COPY --from` form: the
ghcr.io image is a fixed digest containing a prebuilt static binary, whereas
`pip install uv==0.5.11` resolves a wheel at build time. The version is pinned
exactly, so the resolution is deterministic in practice, but it is not
hash-pinned. Hash pinning the build tooling is the correct next step and needs a
lockfile for build-time dependencies, which does not exist yet; it is recorded
here rather than done now because it is a separate piece of work with its own
maintenance cost.

The rule generalises past `uv`. The next base image, scanner or CLI adopted from
a blog post will name `ghcr.io` too, and this ADR is the reason to check before
copying it in.
