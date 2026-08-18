"""Domain layer (SRS §8.2 layer 3).

Pure functions over value objects. NO ORM imports, NO I/O, no Django.
Enforced by import-linter contract 'domain-layer-is-pure'.

This is the audit-sensitive logic — pricing, policy evaluation,
validation rules and state-machine guards — and it carries the 95%
coverage gate (SRS §35.3, §36.2).
"""
