"""Repair drifted external_work_refs (missing idempotency_key / source_event_id).

An older external_work_refs table (uniqueness on external_id/external_url) already
existed on long-lived schemas, so the ``CREATE TABLE IF NOT EXISTS`` in
20260613_0002 skipped it: its ``idempotency_key`` / ``source_event_id`` columns and
the idempotency unique index the upsert's ``ON CONFLICT`` relies on were never
added. This forward migration brings any such table up to the current shape and is
a no-op on a freshly created schema.

Revision ID: 20260614_0003
Revises: 20260613_0002
Create Date: 2026-06-14
"""

from __future__ import annotations

from alembic import op

revision = "20260614_0003"
down_revision = "20260613_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE external_work_refs
            ADD COLUMN IF NOT EXISTS idempotency_key TEXT NOT NULL DEFAULT '',
            ADD COLUMN IF NOT EXISTS source_event_id TEXT NOT NULL DEFAULT '';

        -- Backfill a guaranteed-unique key (the PK) for any legacy rows so the
        -- idempotency unique index below builds without collisions.
        UPDATE external_work_refs
           SET idempotency_key = id::text
         WHERE idempotency_key = '';

        -- Replace the obsolete wide unique (legacy schemas only) with the
        -- idempotency-key unique the ON CONFLICT upsert depends on.
        ALTER TABLE external_work_refs
            DROP CONSTRAINT IF EXISTS
                external_work_refs_run_id_work_item_id_system_external_type_key;

        CREATE UNIQUE INDEX IF NOT EXISTS uq_external_work_refs_idem
            ON external_work_refs(run_id, work_item_id, system, external_type, idempotency_key);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_external_work_refs_idem;")
