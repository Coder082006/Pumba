# ADR 0003 — The `system_setting` read port lives in `common`

**Status:** Accepted · **Date:** 2026-08-18 · **Phase:** 1

## Context

SRS §6.4 assigns the `system_setting` table to the `administration` module and
lists administration's dependencies as **"all (read via interfaces)"**.

NFR-M07 and brief rule 5 require that *no* business constant is hard-coded:
every threshold, rate, weight and TTL is a `system_setting` row. All 30 keys
are listed in SRS Appendix B, and they are read across the whole system —
pricing reads `platform_fee_rate`, transport reads `dispatch.weights`,
inventory reads `quote.ttl_minutes`, trip reads `trip.max_days`.

So every module must read from `administration`, and `administration` depends
on every module. That is a cycle. It cannot be expressed in the import-linter
contracts SRS §6.5 rule 2 mandates, and it defeats the seam-preservation that
SRS §6.2 gives as the whole justification for the modular monolith.

## Decision

Split read from write.

- `apps/common/config.py` is the **read** port: `get_setting(key)`. `common` is
  a leaf every module may import, so no cycle exists.
- `administration` keeps the `system_setting` table, the audited write path
  (SRS §30.12) and the console UI, and depends on all modules as §6.4 says. It
  registers a database-backed provider into the read port at startup.
- The Appendix B register with its defaults lives in the read port, so lookups
  resolve correctly before the table exists.
- An unregistered key raises `UnknownSettingError` rather than returning
  `None`, so a typo cannot silently disable a business rule.

## Consequences

§6.4's intent is preserved — one owner, audited writes, no constant in code —
while the dependency graph stays a DAG. `administration` still owns the data;
it does not own the accessor.

The cost is that the register is declared in `common` while the table is
declared in `administration`, so adding a setting touches two modules. That is
a small price for a graph that can be mechanically enforced, and the register
doubles as the single readable list of every tunable in the system.

SRS §6.4 should be amended to note that the read path is a shared-kernel
concern.
