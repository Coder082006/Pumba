# ADR 0014 — Module privacy contracts check direct imports, not reachability

**Status:** Accepted · **Date:** 2026-08-23 · **Phase:** 3

## Context

SRS §6.5 rule 1 states the module seam:

> Cross-module imports are permitted only from `apps.<module>.services` and
> `apps.<module>.dto` — never from `apps.<module>.models`.

Rule 2 makes it mechanical: *"import-linter contracts encode the dependency
table above; a forbidden import fails the build."* Fourteen `private-<module>`
contracts in `apps/api/.importlinter` encode rule 1, one per module, each
listing every *other* module as a source and that module's non-public
submodules — `models`, `repositories`, `selectors`, `domain`, `views`,
`serializers`, `permissions`, `urls`, `tasks`, `adapters` — as forbidden.

Those contracts were written in Phase 1 and have been green ever since. They
were also, as encoded, unsatisfiable.

import-linter's `forbidden` contract defaults to reporting **any import chain**
from a source module to a forbidden module, not just a direct import. A
module's `services.py` necessarily imports its own models, repositories and
domain — that is what an application layer is. So the chain

    apps.administration.…seed  →  apps.catalogue.services  →  apps.catalogue.models

is a violation of `private-catalogue` under the default semantics, and it is
produced by doing exactly what rule 1 *requires*. Every route to another
module's data was forbidden: the illegitimate one directly, and the sanctioned
one through the chain it cannot avoid. A rule that forbids its own compliance
path is not a strict rule; it is a broken one.

Nobody discovered this for twenty-eight commits of Phase 3 and the whole of
Phases 1 and 2, because until the Appendix C seed loader (commit 28) **no code
had ever made a cross-module `services.py` call.** Each module had been built
and tested against its own internals. The contracts passed the entire time and
proved nothing about the property they exist to protect — the first attempt to
comply broke them.

That is the part worth recording. A green control that has never been exercised
is indistinguishable from a green control that works, and the distinction only
appears at the moment somebody needs the thing it was guarding.

## Decision

**Every `private-*` contract sets `allow_indirect_imports = True`.** The
contracts assert that no module *names* another module's internals in an import
statement. They no longer assert anything about reachability.

This is the semantics rule 1 actually describes. §6.5 rule 1 is a sentence about
import statements — "cross-module imports are permitted only from …" — and its
companion rule 3 makes the separation explicit by governing *reading* through a
different mechanism:

> Foreign keys across modules are permitted (single database) but the reading
> module must not traverse the relation to read the other module's columns; it
> calls the service.

Rules 1 and 3 divide the work: rule 1 is about what a file may import, rule 3 is
about what a module may read. Reachability-based contracts collapsed the two and
made rule 1 answer for both, which is why it became unsatisfiable — the only
program that satisfies a transitive reading of rule 1 is one where no module
ever calls another.

What still fails, unchanged:

```python
from apps.catalogue.models import Destination   # private-catalogue: broken
from apps.catalogue import selectors            # private-catalogue: broken
```

What now passes, and could not before:

```python
from apps.catalogue import services as catalogue   # SRS §6.5 rule 1
```

The dependency contracts of §6.4 (`deps-*`) and the layer contracts of §8.2 are
**not** changed. Those are genuinely about reachability: `inventory` must not
end up depending on `location` by any route, which is the property that makes
the seams extractable later (§6.2, §44.2). Only the privacy contracts move, and
only because privacy is a statement about names.

## Verification

The change weakens fourteen contracts, so it was mutation-tested rather than
trusted:

    from apps.catalogue.models import Destination   # added to seed.py

produces **30 kept, 1 broken** — `private-catalogue` fails, naming the file and
the import. Removing the line returns 31 kept. The contracts still catch the
thing they are for.

## Consequences

**"31 contracts kept" now asserts something narrower than it did in Phase 1**,
and a later reader comparing two build reports deserves to know why. Before this
ADR the privacy contracts asserted "no module can reach another module's
internals by any path"; that reading was vacuously true, since no cross-module
call existed to violate it, and it would have become false the instant one did.
After it they assert "no module names another module's internals". The second
statement is weaker, checkable, and is the one §6.5 rule 1 makes.

**What the weakening gives up is real.** A module could now import
`apps.catalogue.services`, receive a DTO, and — if a service ever returned an
ORM instance — reach through it into `catalogue`'s tables without ever naming
`apps.catalogue.models`. import-linter cannot see that; it reads import
statements, not values.

That gap is exactly what §6.5 rule 5 already assigns to a different control:

> **Architecture test.** A test asserts that every module's `services.py`
> exposes only DTOs and primitives — never ORM instances — across module
> boundaries.

So the property is not unguarded, it is guarded by a different instrument. A
linter checks names; `test_services_never_return_orm_instances` in
`tests/test_architecture.py` checks signatures — it resolves the return
annotation of every public function in every module's `services.py` and fails if
any of them is a `Model` or a `QuerySet`, unwrapping `Optional`, `list` and
unions on the way.

This ADR raises the weight that test carries, and its limit should be stated
plainly rather than discovered later. It reads **annotations, not values**: a
service function annotated `-> Any`, left unannotated, or returning a dataclass
that happens to hold a `Model` in a field passes it. That is a narrower
guarantee than "no ORM instance crosses a seam", and it is now the widest
guarantee we have on that property. Two things follow — a service function in
this codebase must carry a real return annotation for the control to see it at
all, and a DTO must not hold an ORM instance in a field. Neither is currently
enforced; both are worth enforcing the next time this area is touched.

**The dependency table is still re-derived from the SRS.**
`tests/test_srs_module_catalogue.py` reads §6.4 out of `docs/srs/srs-docx.txt`
on every run and fails if `.importlinter` drifts from it. That test is unaffected
— it checks which modules appear in which contract, not the contract's
comparison semantics — so this change cannot silently widen who may depend on
whom.

**The general lesson, for the phases that add the other thirteen cross-module
calls.** Phase 4 onward will make `inventory → catalogue`, `trip → transport`,
`booking → inventory` and the rest into real calls for the first time. Each one
is a first exercise of a contract that has been green without being tested. The
expectation should be that some of them fail on contact, and that a failure at
that moment is information about the contract rather than about the call.
