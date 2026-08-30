"""Native ticketing foundation: ticket types, orders, tickets, webhook idempotency

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-30

The biggest schema addition in the app's history, and still purely
additive — five new tables, four new nullable-or-defaulted columns on
existing tables, nothing existing touched or narrowed.

New tables:
- ticket_types            what's for sale (price/quantity per event,
                          optionally linked to a seating category so paid
                          sales and guest-list holds share one pool)
- orders                  one purchase attempt; money amounts and the
                          organization are SNAPSHOTS at creation
- order_items             lines of an order; unit price and name snapshotted
- tickets                 one row per admission, unique code each,
                          nullable seat_id hook for the future per-seat world
- stripe_webhook_events   processed-event ledger; the unique constraint IS
                          the idempotency (Stripe redelivers events by design)

Existing-table additions:
- seating_categories.sales_grain   'ga' (default, backfilled) | 'seat' —
                                   per-category because one room can mix
                                   assigned rows with GA rows
- event_profiles.refund_policy     organizer's own words, shown at checkout
- event_profiles.venue_map_url     uploaded venue-map image (v1 of seat maps)
- event_profiles.venue_layout      JSON home for future structured layouts
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seating_category_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("seating_categories.id"), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="usd"),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("max_per_order", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("sales_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sales_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ticket_types_event_id", "ticket_types", ["event_id"])

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "PAID", "EXPIRED", "REFUNDED", name="orderstatus"),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("buyer_name", sa.String(), nullable=False),
        sa.Column("buyer_email", sa.String(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="usd"),
        sa.Column("subtotal_cents", sa.Integer(), nullable=False),
        sa.Column("platform_fee_cents", sa.Integer(), nullable=False),
        sa.Column("organizer_net_cents", sa.Integer(), nullable=False),
        sa.Column("promo_code_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("promo_codes.id"), nullable=True),
        sa.Column("stripe_checkout_session_id", sa.String(), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(), nullable=True),
        sa.Column("order_token", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_orders_event_id", "orders", ["event_id"])
    op.create_index("ix_orders_organization_id", "orders", ["organization_id"])
    op.create_index("ix_orders_buyer_email", "orders", ["buyer_email"])
    op.create_index("ix_orders_stripe_checkout_session_id", "orders", ["stripe_checkout_session_id"], unique=True)
    op.create_index("ix_orders_order_token", "orders", ["order_token"], unique=True)

    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("ticket_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ticket_types.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("ticket_type_name", sa.String(), nullable=False),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_ticket_type_id", "order_items", ["ticket_type_id"])

    op.create_table(
        "tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("order_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("order_items.id"), nullable=False),
        sa.Column("ticket_type_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ticket_types.id"), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("status", sa.Enum("VALID", "REFUNDED", name="ticketstatus"), nullable=False, server_default="VALID"),
        sa.Column("seat_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_tickets_order_id", "tickets", ["order_id"])
    op.create_index("ix_tickets_ticket_type_id", "tickets", ["ticket_type_id"])
    op.create_index("ix_tickets_event_id", "tickets", ["event_id"])
    op.create_index("ix_tickets_code", "tickets", ["code"], unique=True)

    op.create_table(
        "stripe_webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("stripe_event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_stripe_webhook_events_stripe_event_id", "stripe_webhook_events", ["stripe_event_id"], unique=True)

    op.add_column("seating_categories", sa.Column("sales_grain", sa.String(), nullable=False, server_default="ga"))
    op.add_column("event_profiles", sa.Column("refund_policy", sa.Text(), nullable=True))
    op.add_column("event_profiles", sa.Column("venue_map_url", sa.String(), nullable=True))
    op.add_column("event_profiles", sa.Column("venue_layout", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("event_profiles", "venue_layout")
    op.drop_column("event_profiles", "venue_map_url")
    op.drop_column("event_profiles", "refund_policy")
    op.drop_column("seating_categories", "sales_grain")
    op.drop_index("ix_stripe_webhook_events_stripe_event_id", table_name="stripe_webhook_events")
    op.drop_table("stripe_webhook_events")
    op.drop_index("ix_tickets_code", table_name="tickets")
    op.drop_index("ix_tickets_event_id", table_name="tickets")
    op.drop_index("ix_tickets_ticket_type_id", table_name="tickets")
    op.drop_index("ix_tickets_order_id", table_name="tickets")
    op.drop_table("tickets")
    op.drop_index("ix_order_items_ticket_type_id", table_name="order_items")
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_order_token", table_name="orders")
    op.drop_index("ix_orders_stripe_checkout_session_id", table_name="orders")
    op.drop_index("ix_orders_buyer_email", table_name="orders")
    op.drop_index("ix_orders_organization_id", table_name="orders")
    op.drop_index("ix_orders_event_id", table_name="orders")
    op.drop_table("orders")
    op.drop_index("ix_ticket_types_event_id", table_name="ticket_types")
    op.drop_table("ticket_types")
    sa.Enum(name="orderstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="ticketstatus").drop(op.get_bind(), checkfirst=True)