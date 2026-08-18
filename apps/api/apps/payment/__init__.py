"""payment module — SRS §6.4.

Owns:       payment, payment_transaction, refund, payment_webhook_event
Interface:  initiate(), verify(), refund()
Depends on: booking, trip, inventory
Layer:      L5

SRS §6.4 says 'booking (via events)' only. But §9.4.7 reads trip.status
and trip.total_amount synchronously, and §20.8's confirmation routine
writes to booking and inventory inside the webhook transaction. The
contract reflects §9.4/§20.8. Revisit at Phase 8 — issue S3 in
docs/IMPLEMENTATION-PLAN.md.
"""
