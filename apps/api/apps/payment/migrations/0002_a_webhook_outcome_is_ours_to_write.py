"""The PSP's words are immutable; our note about them is not.

`payment_webhook_event` was append-only outright, which is right for the
payload — §21.5 stores it before anything reads it precisely so a processing
bug can never lose an event — and wrong for `outcome`, `processed_at` and
`note`, which are the platform's record of what it *did* about the event and
cannot be known until after the row exists.

The trigger is narrowed rather than dropped: every column the PSP supplied
stays frozen, and an UPDATE that touches one still raises. Only the
bookkeeping columns may move — `outcome`, `processed_at`, `note` and the
`payment_id` the match resolves — and DELETE is refused as before.

The alternative was a second table for outcomes, which would have split one
event's story across two rows for the sake of a rule that was never about
those columns.
"""

from django.db import migrations

NARROWED = """
CREATE OR REPLACE FUNCTION payment_webhook_event_is_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'payment_webhook_event is append-only (SRS §21.5): DELETE refused';
    END IF;

    IF NEW.psp_name       IS DISTINCT FROM OLD.psp_name
    OR NEW.psp_event_id   IS DISTINCT FROM OLD.psp_event_id
    OR NEW.event_type     IS DISTINCT FROM OLD.event_type
    OR NEW.psp_reference  IS DISTINCT FROM OLD.psp_reference
    OR NEW.payload        IS DISTINCT FROM OLD.payload
    OR NEW.signature_verified IS DISTINCT FROM OLD.signature_verified
    OR NEW.received_at    IS DISTINCT FROM OLD.received_at THEN
        RAISE EXCEPTION
            'payment_webhook_event records the gateway verbatim (SRS §21.5): '
            'only outcome, processed_at, note and payment_id may change';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

OUTRIGHT = """
CREATE OR REPLACE FUNCTION payment_webhook_event_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'payment_webhook_event is append-only (SRS §21.5): % refused', TG_OP;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [("payment", "0001_initial")]

    operations = [migrations.RunSQL(NARROWED, OUTRIGHT)]
