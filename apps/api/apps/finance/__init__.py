"""finance module — SRS §6.4.

Owns:       commission_rule, ledger_entry, provider_balance, payout, payout_item, fx_rate
Interface:  accrue(), settle(), build_payout_batch()
Depends on: payment, booking
Layer:      L6

Ledger is append-only: no UPDATE, no DELETE on financial rows.
Corrections are new reversing entries (SRS §22.3, principle A2).
"""
