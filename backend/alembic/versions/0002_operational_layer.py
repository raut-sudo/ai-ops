"""0002_operational_layer - Create operational business data layer (Layer 1).

Revision ID: 0002_operational_layer
Revises: 0001_agent_output_layer
Create Date: 2026-06-11

This migration creates the 10 operational tables that represent the live store.
These are queryable by domain tools and writable by the action execution loop.

FROZEN SCHEMA per Blueprint §11 (R3.3).
All timestamps TIMESTAMPTZ. All money NUMERIC(10,2).
FK order: products → inventory, customers, campaigns → orders → order_items, etc.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_operational_layer"
down_revision = "0001_agent_output_layer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── Catalog: Products ───────────────────────────────────────────────
    op.create_table(
        "products",
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("subcategory", sa.String(100), nullable=True),
        sa.Column("brand", sa.String(100), nullable=True),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("sku"),
    )
    op.create_index("idx_products_category", "products", ["category"])
    op.create_index(
        "idx_products_active",
        "products",
        ["is_active"],
        postgresql_where=sa.text("is_active = TRUE"),
    )

    # ─── Catalog: Customers ──────────────────────────────────────────────
    op.create_table(
        "customers",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("region", sa.String(50), nullable=False),
        sa.Column("customer_segment", sa.String(50), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_customers_region", "customers", ["region"])

    # ─── Sales: Orders ───────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("order_number", sa.String(50), nullable=False, unique=True),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("region", sa.String(50), nullable=False),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("shipping_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_orders_placed_at",
        "orders",
        [sa.desc("placed_at")],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_orders_status_placed",
        "orders",
        ["status", sa.desc("placed_at")],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_orders_region_placed",
        "orders",
        ["region", sa.desc("placed_at")],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_orders_campaign",
        "orders",
        ["campaign_id"],
        postgresql_where=sa.text("campaign_id IS NOT NULL"),
    )

    # ─── Sales: Order Items ──────────────────────────────────────────────
    op.create_table(
        "order_items",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("line_total", sa.Numeric(10, 2), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku"], ["products.sku"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0"),
    )
    op.create_index("idx_order_items_sku", "order_items", ["sku"])
    op.create_index("idx_order_items_order", "order_items", ["order_id"])

    # ─── Inventory: Snapshot ─────────────────────────────────────────────
    op.create_table(
        "inventory",
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("warehouse_id", sa.String(50), nullable=False, server_default="WH-MAIN"),
        sa.Column("quantity_on_hand", sa.Integer(), nullable=False),
        sa.Column("quantity_reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reorder_point", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("reorder_quantity", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("last_restocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["sku"], ["products.sku"]),
        sa.PrimaryKeyConstraint("sku"),
        sa.CheckConstraint("quantity_on_hand >= 0"),
    )

    # ─── Inventory: Movements (audit log) ────────────────────────────────
    op.create_table(
        "inventory_movements",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("movement_type", sa.String(50), nullable=False),
        sa.Column("quantity_change", sa.Integer(), nullable=False),
        sa.Column("quantity_after", sa.Integer(), nullable=False),
        sa.Column("reference_type", sa.String(50), nullable=True),
        sa.Column("reference_id", sa.String(255), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["sku"], ["products.sku"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_inv_movements_sku_time",
        "inventory_movements",
        ["sku", sa.desc("occurred_at")],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_inv_movements_time",
        "inventory_movements",
        [sa.desc("occurred_at")],
        postgresql_using="btree",
    )

    # ─── Marketing: Campaigns ────────────────────────────────────────────
    op.create_table(
        "campaigns",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("budget_total", sa.Numeric(10, 2), nullable=False),
        sa.Column("budget_spent", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("target_skus", sa.ARRAY(sa.String(50)), nullable=True),
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_campaigns_status", "campaigns", ["status"])
    op.create_index("idx_campaigns_channel", "campaigns", ["channel"])

    # ─── Marketing: Daily Metrics ────────────────────────────────────────
    op.create_table(
        "campaign_metrics_daily",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spend", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column(
            "attributed_revenue",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0",
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["campaigns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "metric_date"),
    )
    op.create_index(
        "idx_camp_metrics_date",
        "campaign_metrics_daily",
        [sa.desc("metric_date")],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_camp_metrics_campaign_date",
        "campaign_metrics_daily",
        ["campaign_id", sa.desc("metric_date")],
        postgresql_using="btree",
    )

    # ─── Add FK from orders to campaigns (now that campaigns exists) ──────
    op.create_foreign_key("fk_orders_campaign", "orders", "campaigns", ["campaign_id"], ["id"])

    # ─── Support: Tickets ────────────────────────────────────────────────
    op.create_table(
        "support_tickets",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("ticket_number", sa.String(50), nullable=False, unique=True),
        sa.Column("customer_id", sa.Uuid(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("related_sku", sa.String(50), nullable=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("sentiment_score", sa.Numeric(3, 2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["related_sku"], ["products.sku"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_tickets_created",
        "support_tickets",
        [sa.desc("created_at")],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_tickets_category_created",
        "support_tickets",
        ["category", sa.desc("created_at")],
        postgresql_using="btree",
    )
    op.create_index(
        "idx_tickets_sku",
        "support_tickets",
        ["related_sku"],
        postgresql_where=sa.text("related_sku IS NOT NULL"),
    )

    # ─── Support: Returns ────────────────────────────────────────────────
    op.create_table(
        "returns",
        sa.Column(
            "id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(50), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("refund_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["sku"], ["products.sku"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity > 0"),
    )
    op.create_index(
        "idx_returns_requested",
        "returns",
        [sa.desc("requested_at")],
        postgresql_using="btree",
    )
    op.create_index("idx_returns_sku", "returns", ["sku"])


def downgrade() -> None:
    # ─── Drop Support tables ─────────────────────────────────────────────
    op.drop_table("returns")
    op.drop_table("support_tickets")

    # ─── Drop Marketing tables ───────────────────────────────────────────
    op.drop_constraint("fk_orders_campaign", "orders", type_="foreignkey")
    op.drop_table("campaign_metrics_daily")
    op.drop_table("campaigns")

    # ─── Drop Inventory tables ───────────────────────────────────────────
    op.drop_table("inventory_movements")
    op.drop_table("inventory")

    # ─── Drop Sales tables ───────────────────────────────────────────────
    op.drop_table("order_items")
    op.drop_table("orders")

    # ─── Drop Catalog tables ─────────────────────────────────────────────
    op.drop_table("customers")
    op.drop_table("products")
