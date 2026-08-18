"""External integration ports.

SRS principle A3: "Every third-party integration sits behind an interface with
at least two conceivable implementations, plus a fake for tests."

SRS §36.2: "apps/*/adapters/ is the only place a third-party SDK may be
imported." These modules declare the *contracts*; the vendor code that
satisfies them lives in `apps/<module>/adapters/` and nowhere else. Both rules
are enforced by import-linter.

**Placement.** SRS §8.2 declares ports in the domain layer of the module that
uses them. They are gathered here instead because several are shared —
`StoragePort` is needed by `catalogue` for media and by `provider` for
verification documents, and locating it inside either would force a dependency
between two modules that SRS §6.4 keeps independent. These are pure protocols
with no I/O, so they satisfy the domain-layer constraint wherever they sit.

**No provider is selected.** SRS Appendix D-1 (payment) and D-2 (routing) are
open commercial decisions. Phase 1 ships protocols and fakes only; real
adapters arrive with the phase that needs them.
"""
