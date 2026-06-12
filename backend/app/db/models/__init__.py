"""SQLAlchemy ORM models for all three data layers (L1, L2, L3).

Layer 1 (Operational):
  - products, customers, orders, order_items
  - inventory, inventory_movements
  - campaigns, campaign_metrics_daily
  - support_tickets, returns

Layer 2 (Agent-State/Checkpoint):
  - sessions, incident_actions, audit_logs

Layer 3 (Long-Term Memory):
  - incidents (dual-write with Qdrant embeddings)

See Blueprint §5 (Three-Layer Data Model), §11 (Operational Layer), §16 (Agent-Output).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declarative_base, mapped_column, relationship

Base = declarative_base()

# ─── Type aliases for common columns ─────────────────────────────────────
TimestampUTC = Annotated[
    datetime,
    mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    ),
]


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 1: OPERATIONAL BUSINESS DATA (the "live store")
# ═══════════════════════════════════════════════════════════════════════════


class Product(Base):
    __tablename__ = "products"

    sku: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    subcategory: Mapped[str | None] = mapped_column(String(100))
    brand: Mapped[str | None] = mapped_column(String(100))
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    cost_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    inventory: Mapped[Inventory | None] = relationship(back_populates="product", uselist=False)
    order_items: Mapped[list[OrderItem]] = relationship(back_populates="product")
    support_tickets: Mapped[list[SupportTicket]] = relationship(back_populates="related_product")
    returns: Mapped[list[Return]] = relationship(back_populates="product")
    inventory_movements: Mapped[list[InventoryMovement]] = relationship(back_populates="product")

    __table_args__ = (
        Index("idx_products_category", "category"),
        Index("idx_products_active", "is_active", postgresql_where=text("is_active = TRUE")),
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    customer_segment: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    orders: Mapped[list[Order]] = relationship(back_populates="customer")
    support_tickets: Mapped[list[SupportTicket]] = relationship(back_populates="customer")

    __table_args__ = (Index("idx_customers_region", "region"),)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    subtotal: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    discount_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    shipping_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    tax_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    customer: Mapped[Customer | None] = relationship(back_populates="orders")
    campaign: Mapped[Campaign | None] = relationship(back_populates="orders")
    order_items: Mapped[list[OrderItem]] = relationship(back_populates="order")
    support_tickets: Mapped[list[SupportTicket]] = relationship(back_populates="order")
    returns: Mapped[list[Return]] = relationship(back_populates="order")

    __table_args__ = (
        Index("idx_orders_placed_at", placed_at.desc()),
        Index("idx_orders_status_placed", "status", placed_at.desc()),
        Index("idx_orders_region_placed", "region", placed_at.desc()),
        Index(
            "idx_orders_campaign", "campaign_id", postgresql_where=text("campaign_id IS NOT NULL")
        ),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    line_total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Relationships
    order: Mapped[Order] = relationship(back_populates="order_items")
    product: Mapped[Product] = relationship(back_populates="order_items")

    __table_args__ = (
        CheckConstraint("quantity > 0"),
        Index("idx_order_items_sku", "sku"),
        Index("idx_order_items_order", "order_id"),
    )


class Inventory(Base):
    __tablename__ = "inventory"

    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), primary_key=True)
    warehouse_id: Mapped[str] = mapped_column(String(50), nullable=False, default="WH-MAIN")
    quantity_on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reorder_point: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    reorder_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    last_restocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    product: Mapped[Product] = relationship(back_populates="inventory")

    __table_args__ = (CheckConstraint("quantity_on_hand >= 0"),)


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity_change: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(50))
    reference_id: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    product: Mapped[Product] = relationship(back_populates="inventory_movements")

    __table_args__ = (
        Index("idx_inv_movements_sku_time", "sku", occurred_at.desc()),
        Index("idx_inv_movements_time", occurred_at.desc()),
    )


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    budget_total: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    budget_spent: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    target_skus: Mapped[list[str] | None] = mapped_column(ARRAY(String(50)), nullable=True)
    discount_percent: Mapped[float | None] = mapped_column(Numeric(5, 2))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    orders: Mapped[list[Order]] = relationship(back_populates="campaign")
    metrics_daily: Mapped[list[CampaignMetricsDaily]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_campaigns_status", "status"),
        Index("idx_campaigns_channel", "channel"),
    )


class CampaignMetricsDaily(Base):
    __tablename__ = "campaign_metrics_daily"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    metric_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    spend: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)
    attributed_revenue: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, default=0)

    # Relationships
    campaign: Mapped[Campaign] = relationship(back_populates="metrics_daily")

    __table_args__ = (
        UniqueConstraint("campaign_id", "metric_date"),
        Index("idx_camp_metrics_date", metric_date.desc()),
        Index("idx_camp_metrics_campaign_date", "campaign_id", metric_date.desc()),
    )


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ticket_number: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    related_sku: Mapped[str | None] = mapped_column(ForeignKey("products.sku"), nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    customer: Mapped[Customer | None] = relationship(back_populates="support_tickets")
    order: Mapped[Order | None] = relationship(back_populates="support_tickets")
    related_product: Mapped[Product | None] = relationship(back_populates="support_tickets")

    __table_args__ = (
        Index("idx_tickets_created", created_at.desc()),
        Index("idx_tickets_category_created", "category", created_at.desc()),
        Index("idx_tickets_sku", "related_sku", postgresql_where=text("related_sku IS NOT NULL")),
    )


class Return(Base):
    __tablename__ = "returns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"), nullable=False)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(100), nullable=False)
    refund_amount: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    order: Mapped[Order] = relationship(back_populates="returns")
    product: Mapped[Product] = relationship(back_populates="returns")

    __table_args__ = (
        CheckConstraint("quantity > 0"),
        Index("idx_returns_requested", requested_at.desc()),
        Index("idx_returns_sku", "sku"),
    )


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 2: AGENT-STATE & CHECKPOINT (per-conversation working memory)
# ═══════════════════════════════════════════════════════════════════════════


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    intent_type: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    incident_actions: Mapped[list[IncidentAction]] = relationship(back_populates="session")

    __table_args__ = (Index("idx_sessions_user", "user_id", updated_at.desc()),)


class IncidentAction(Base):
    __tablename__ = "incident_actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    action_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    incident_id: Mapped[str | None] = mapped_column(ForeignKey("incidents.id"), nullable=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.thread_id"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target: Mapped[str] = mapped_column(String(255), nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="proposed")
    justification: Mapped[str | None] = mapped_column(Text)
    approver: Mapped[str | None] = mapped_column(String(255))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    session: Mapped[Session | None] = relationship(back_populates="incident_actions")
    incident: Mapped[Incident | None] = relationship(back_populates="actions")

    __table_args__ = (
        Index("idx_incident_actions_status", "status"),
        Index("idx_incident_actions_session", "session_id"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String(255))
    user_id: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_audit_event", "event_type", created_at.desc()),)


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 3: LONG-TERM MEMORY (semantic, cross-session)
# ═══════════════════════════════════════════════════════════════════════════


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_causes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    actions_taken: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    outcome: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="open")
    embedded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Relationships
    actions: Mapped[list[IncidentAction]] = relationship(back_populates="incident")

    __table_args__ = (Index("idx_incidents_occurred", occurred_at.desc()),)


__all__ = [
    "AuditLog",
    "Base",
    "Campaign",
    "CampaignMetricsDaily",
    "Customer",
    # Layer 3
    "Incident",
    "IncidentAction",
    "Inventory",
    "InventoryMovement",
    "Order",
    "OrderItem",
    # Layer 1
    "Product",
    "Return",
    # Layer 2
    "Session",
    "SupportTicket",
]
