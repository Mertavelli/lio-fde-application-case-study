"""Persist classification review and explicit approval origin.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("procurement_requests", sa.Column("classification", sa.JSON(), nullable=True))
    op.add_column("procurement_requests", sa.Column("approval_source", sa.String(), nullable=True))
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE procurement_requests AS request
        SET approval_source = CASE WHEN (
            SELECT activity.action FROM request_activity AS activity
            WHERE activity.request_id = request.id
              AND activity.action IN ('auto_approved', 'approved')
            ORDER BY activity.created_at DESC, activity.id DESC LIMIT 1
        ) = 'auto_approved' THEN 'automatic' ELSE 'manual' END
        WHERE request.approval_status = 'approved'
    """))
    orgs = sa.table("organizations", sa.column("id", sa.Uuid()), sa.column("slug", sa.String()), sa.column("settings", sa.JSON()))
    defaults = [
        {"term": "cable ties", "commodity_group_id": 11},
        {"term": "printer toner", "commodity_group_id": 2001},
        {"term": "toner cartridge", "commodity_group_id": 2001},
        {"term": "software subscription", "commodity_group_id": 2002, "max_total": 500},
    ]
    for org_id, settings in bind.execute(sa.select(orgs.c.id, orgs.c.settings).where(orgs.c.slug == "acme")):
        actual = [{key: value for key, value in rule.items() if value is not None}
                  for rule in (settings or {}).get("category_rules", [])]
        if actual == defaults:
            updated = {**settings, "category_rules": [
                {"item_kind": "cable_tie", "commodity_group_id": 11},
                {"item_kind": "printer_toner", "commodity_group_id": 2001},
                {"item_kind": "software_subscription", "commodity_group_id": 2002, "max_total": 500},
            ]}
            bind.execute(orgs.update().where(orgs.c.id == org_id).values(settings=updated))


def downgrade() -> None:
    bind = op.get_bind()
    orgs = sa.table("organizations", sa.column("id", sa.Uuid()), sa.column("slug", sa.String()), sa.column("settings", sa.JSON()))
    defaults = [
        {"item_kind": "cable_tie", "commodity_group_id": 11},
        {"item_kind": "printer_toner", "commodity_group_id": 2001},
        {"item_kind": "software_subscription", "commodity_group_id": 2002, "max_total": 500},
    ]
    for org_id, settings in bind.execute(sa.select(orgs.c.id, orgs.c.settings).where(orgs.c.slug == "acme")):
        actual = [{key: value for key, value in rule.items() if value is not None}
                  for rule in (settings or {}).get("category_rules", [])]
        if actual == defaults:
            updated = {**settings, "category_rules": [
                {"term": "cable ties", "commodity_group_id": 11},
                {"term": "printer toner", "commodity_group_id": 2001},
                {"term": "toner cartridge", "commodity_group_id": 2001},
                {"term": "software subscription", "commodity_group_id": 2002, "max_total": 500},
            ]}
            bind.execute(orgs.update().where(orgs.c.id == org_id).values(settings=updated))
    op.drop_column("procurement_requests", "approval_source")
    op.drop_column("procurement_requests", "classification")
