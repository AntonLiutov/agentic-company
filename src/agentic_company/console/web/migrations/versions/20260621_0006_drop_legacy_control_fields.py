"""Collapse the control plane to two fields: drop legacy mode/complexity/team_preset.

The run shape is carried entirely by ``runs.run_mode`` (its roster + pipeline policy live
in ``mode_policy``); approval by ``runs.risk_mode``. The old ``projects.mode`` /
``projects.complexity`` / ``runs.mode`` / ``runs.team_preset`` columns were duplicate
representations of the same axis and are removed.

Revision ID: 20260621_0006
Revises: 20260620_0005
Create Date: 2026-06-21
"""

from __future__ import annotations

from alembic import op

revision = "20260621_0006"
down_revision = "20260620_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runs
            DROP COLUMN IF EXISTS team_preset,
            DROP COLUMN IF EXISTS mode;

        ALTER TABLE projects
            DROP COLUMN IF EXISTS mode,
            DROP COLUMN IF EXISTS complexity;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS complexity TEXT NOT NULL DEFAULT 'simple',
            ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'simple_prototype';

        ALTER TABLE runs
            ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'simple_prototype',
            ADD COLUMN IF NOT EXISTS team_preset TEXT NOT NULL DEFAULT 'standard';
        """
    )
