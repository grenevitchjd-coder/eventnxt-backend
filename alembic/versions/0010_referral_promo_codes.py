"""Referral promo codes, sales reconciliation, and per-event sales platform config

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-27

Additive only — three new tables, nothing existing touched.

Design summary: a PromoCode belongs to a referrer (an existing Guest) and
carries its own reward terms (flat amount / percentage / free tickets),
so one person can hold several codes with different deals. A Sale is a
normalized record of a reconciled purchase — buyer info, amount, which
code (if any) was used, and where the record came from (source) — every
ingestion path (CSV today, a live platform integration or native sales
later) produces the same shape here, which is what keeps promo-code
attribution and reward math identical regardless of source. SalesConfig
is one row per event recording which box-office platform the organizer
says they're using; it drives which reconciliation UI is offered, but
every platform can fall back to CSV since no live integrations exist yet.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sales_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "platform",
            postgresql.ENUM(
                "custom_csv", "eventbrite", "ticketmaster", "square", "stripe", "other", name="sales_platform"
            ),
            nullable=False,
            server_default="custom_csv",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sales_configs_event_id", "sales_configs", ["event_id"], unique=True)

    op.create_table(
        "promo_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guest_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("guests.id"), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column(
            "reward_type",
            postgresql.ENUM("flat_amount", "percentage", "free_tickets", name="reward_type"),
            nullable=False,
        ),
        sa.Column("reward_value", sa.Numeric(), nullable=False),
        sa.Column("referral_message_draft", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_promo_codes_event_id", "promo_codes", ["event_id"])
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"])
    op.create_unique_constraint("uq_promo_codes_event_code", "promo_codes", ["event_id", "code"])

    op.create_table(
        "sales",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("promo_code_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promo_codes.id"), nullable=True),
        sa.Column("buyer_name", sa.String(), nullable=True),
        sa.Column("buyer_email", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(), nullable=True),
        sa.Column("sale_date", sa.String(), nullable=True),
        sa.Column("external_transaction_id", sa.String(), nullable=True),
        sa.Column(
            "source",
            postgresql.ENUM("csv_upload", "live_api", "native", name="sale_source"),
            nullable=False,
            server_default="csv_upload",
        ),
        sa.Column("computed_reward", sa.Numeric(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sales_event_id", "sales", ["event_id"])
    op.create_index("ix_sales_external_transaction_id", "sales", ["external_transaction_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_external_transaction_id", table_name="sales")
    op.drop_index("ix_sales_event_id", table_name="sales")
    op.drop_table("sales")

    op.drop_constraint("uq_promo_codes_event_code", "promo_codes", type_="unique")
    op.drop_index("ix_promo_codes_code", table_name="promo_codes")
    op.drop_index("ix_promo_codes_event_id", table_name="promo_codes")
    op.drop_table("promo_codes")

    op.drop_index("ix_sales_configs_event_id", table_name="sales_configs")
    op.drop_table("sales_configs")

    op.execute("DROP TYPE IF EXISTS sale_source")
    op.execute("DROP TYPE IF EXISTS reward_type")
    op.execute("DROP TYPE IF EXISTS sales_platform")