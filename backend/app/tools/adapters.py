from __future__ import annotations

import uuid

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.schemas import (
    ActionProposal,
    ActionRequest,
    AlertParams,
    CampaignParams,
    DiscountParams,
    RestockParams,
    TicketParams,
)
from app.tools.actions import (
    create_discount_offer,
    create_purchase_order,
    notify_stakeholders,
    open_customer_issue,
    resume_campaign,
    suspend_campaign,
)
from app.tools.inventory import (
    analyze_inventory,
    get_inventory_movements,
    get_inventory_turnover,
    get_product_details,
    get_products_near_reorder_point,
    get_restock_history,
    get_revenue_lost_to_stockouts,
    get_slow_moving_products,
    get_stock_level,
    get_stockout_history,
)
from app.tools.marketing import (
    analyze_marketing,
    get_campaign_by_channel,
    get_campaign_daily_trend,
    get_campaigns_for_sku,
    get_campaigns_near_budget_exhaustion,
    get_discount_impact,
    get_underperforming_campaigns,
    get_unpromoted_top_products,
)
from app.tools.sales import (
    analyze_sales,
    get_campaign_revenue_attribution,
    get_customer_segment_breakdown,
    get_declining_products,
    get_hourly_sales_trend,
    get_orders_by_status,
    get_sales_by_sku,
    get_sales_distribution,
    get_top_products,
)
from app.tools.support import (
    analyze_support,
    get_churn_risk_products,
    get_common_complaint_categories,
    get_common_return_reasons,
    get_open_tickets_snapshot,
    get_products_with_high_complaint_rate,
    get_return_rate_by_sku,
    get_sentiment_trend,
    get_ticket_resolution_times,
)

# ── Sales ──────────────────────────────────────────────────────────────────


class AnalyzeSalesArgs(BaseModel):
    period: str = Field(description="Time window: yesterday, last_7_days, last_30_days")
    region: str | None = Field(default=None, description="Filter by region")
    sku: str | None = Field(default=None, description="Filter by SKU")
    compare_previous: bool = Field(default=True, description="Include delta vs previous window")


class TopProductsArgs(BaseModel):
    period: str
    limit: int = Field(default=10, ge=1, le=100)


class DecliningProductsArgs(BaseModel):
    period: str
    limit: int = Field(default=10, ge=1, le=100)


class SalesDistributionArgs(BaseModel):
    period: str
    group_by: str = Field(default="region", description="region or channel")


class SalesBySkuArgs(BaseModel):
    sku: str
    period: str


class CustomerSegmentBreakdownArgs(BaseModel):
    period: str


class OrdersByStatusArgs(BaseModel):
    period: str


class CampaignRevenueAttributionArgs(BaseModel):
    period: str


class HourlySalesTrendArgs(BaseModel):
    date: str = Field(description="ISO date string YYYY-MM-DD")


# ── Inventory ─────────────────────────────────────────────────────────────


class AnalyzeInventoryArgs(BaseModel):
    pass


class StockLevelArgs(BaseModel):
    sku: str


class StockoutHistoryArgs(BaseModel):
    sku: str
    period: str


class InventoryTurnoverArgs(BaseModel):
    period: str


class RevenueLostToStockoutsArgs(BaseModel):
    period: str


class RestockHistoryArgs(BaseModel):
    sku: str
    period: str


class ProductsNearReorderArgs(BaseModel):
    buffer_pct: float = Field(
        default=20.0,
        ge=0,
        le=100,
        description="How far above reorder point to include, as a percentage",
    )


class SlowMovingProductsArgs(BaseModel):
    period: str
    turnover_threshold: float = Field(
        default=0.1, ge=0, description="Max turnover ratio to qualify as slow-moving"
    )


class InventoryMovementsArgs(BaseModel):
    sku: str
    period: str


class ProductDetailsArgs(BaseModel):
    sku: str


# ── Marketing ─────────────────────────────────────────────────────────────


class AnalyzeMarketingArgs(BaseModel):
    period: str


class UnderperformingCampaignArgs(BaseModel):
    period: str
    roas_threshold: float = 1.0


class CampaignsForSkuArgs(BaseModel):
    sku: str


class UnpromotedTopProductsArgs(BaseModel):
    period: str
    limit: int = Field(default=10, ge=1, le=100)


class CampaignByChannelArgs(BaseModel):
    period: str


class CampaignsNearBudgetExhaustionArgs(BaseModel):
    pct_threshold: float = Field(
        default=90.0, ge=0, le=100, description="Budget-spent percentage threshold"
    )


class CampaignDailyTrendArgs(BaseModel):
    campaign_id: str
    period: str


class DiscountImpactArgs(BaseModel):
    period: str


# ── Support ───────────────────────────────────────────────────────────────


class AnalyzeSupportArgs(BaseModel):
    period: str


class HighComplaintRateArgs(BaseModel):
    period: str
    min_complaints: int = Field(default=1, ge=1)


class ComplaintCategoriesArgs(BaseModel):
    period: str


class ReturnReasonsArgs(BaseModel):
    period: str


class ChurnRiskProductsArgs(BaseModel):
    period: str
    sentiment_threshold: float = Field(default=-0.2)
    min_returns: int = Field(default=1, ge=0)


class TicketResolutionTimesArgs(BaseModel):
    period: str


class OpenTicketsSnapshotArgs(BaseModel):
    pass


class SentimentTrendArgs(BaseModel):
    period: str


class ReturnRateBySkuArgs(BaseModel):
    sku: str
    period: str


# ── Action schemas ────────────────────────────────────────────────────────


class CreatePurchaseOrderArgs(BaseModel):
    sku: str = Field(description="SKU to restock")
    quantity: int = Field(gt=0, description="Number of units to add to inventory")


class SuspendCampaignArgs(BaseModel):
    campaign_id: str = Field(description="ID of the campaign to suspend")


class ResumeCampaignArgs(BaseModel):
    campaign_id: str = Field(description="ID of the campaign to resume")


class CreateDiscountOfferArgs(BaseModel):
    sku: str = Field(description="SKU to discount")
    percent: float = Field(gt=0, le=90, description="Discount percentage")


class OpenCustomerIssueArgs(BaseModel):
    subject: str = Field(description="Short description of the issue")
    priority: str = Field(description="Ticket priority: low, medium, or high")


class NotifyStakeholdersArgs(BaseModel):
    channel: str = Field(description="Notification channel (e.g. slack, email, pagerduty)")
    message: str = Field(description="Alert message body")


# ── Action adapters ───────────────────────────────────────────────────────


async def _adapter_create_purchase_order(sku: str, quantity: int):
    proposal = ActionProposal(
        domain="inventory",
        target=sku,
        parameters=RestockParams(sku=sku, quantity=quantity),
        risk_level="medium",
        justification="Increase inventory availability for the requested SKU.",
        estimated_impact=f"Add {quantity} units to {sku} stock.",
    )
    return await create_purchase_order(proposal)


async def _adapter_suspend_campaign(campaign_id: str):
    proposal = ActionProposal(
        domain="marketing",
        target=campaign_id,
        parameters=CampaignParams(
            action_type="suspend_campaign",
            campaign_id=campaign_id,
        ),
        risk_level="medium",
        justification="Suspend underperforming campaign to control spend.",
        estimated_impact="Stop spend and impressions for the campaign.",
    )
    return await suspend_campaign(proposal)


async def _adapter_resume_campaign(campaign_id: str):
    proposal = ActionProposal(
        domain="marketing",
        target=campaign_id,
        parameters=CampaignParams(
            action_type="resume_campaign",
            campaign_id=campaign_id,
        ),
        risk_level="medium",
        justification="Reactivate campaign to restore demand generation.",
        estimated_impact="Resume reach and conversions for the campaign.",
    )
    return await resume_campaign(proposal)


async def _adapter_create_discount_offer(sku: str, percent: float):
    proposal = ActionProposal(
        domain="marketing",
        target=sku,
        parameters=DiscountParams(sku=sku, percent=percent),
        risk_level="medium",
        justification="Create a discount offer to improve conversion for the SKU.",
        estimated_impact=f"Apply {percent}% discount to {sku}.",
    )
    return await create_discount_offer(proposal)


async def _adapter_open_customer_issue(subject: str, priority: str):
    proposal = ActionProposal(
        domain="support",
        target="support",
        parameters=TicketParams(subject=subject, priority=priority),
        risk_level="low",
        justification="Open a customer issue for follow-up.",
        estimated_impact="Create a support ticket for resolution.",
    )
    return await open_customer_issue(proposal)


async def _adapter_notify_stakeholders(channel: str, message: str):
    proposal = ActionProposal(
        domain="support",
        target=channel,
        parameters=AlertParams(channel=channel, message=message),
        risk_level="low",
        justification="Notify stakeholders of an operational event.",
        estimated_impact="Deliver alert to the specified channel.",
    )
    return await notify_stakeholders(proposal)


# ── Tool lists ────────────────────────────────────────────────────────────


READ_TOOLS = [
    # Sales
    StructuredTool.from_function(
        coroutine=analyze_sales,
        name="analyze_sales",
        description="Return revenue, order count, AOV, units sold, and period-over-period deltas. Optionally filter by region or SKU.",
        args_schema=AnalyzeSalesArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_top_products,
        name="get_top_products",
        description="Get top-selling products ranked by units sold for a period.",
        args_schema=TopProductsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_declining_products,
        name="get_declining_products",
        description="Find products whose revenue or units declined versus the previous window.",
        args_schema=DecliningProductsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_sales_distribution,
        name="get_sales_distribution",
        description="Break down order count and revenue by region or channel.",
        args_schema=SalesDistributionArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_sales_by_sku,
        name="get_sales_by_sku",
        description="Return units sold, revenue, and period-over-period deltas for a single SKU.",
        args_schema=SalesBySkuArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_customer_segment_breakdown,
        name="get_customer_segment_breakdown",
        description="Break down orders and revenue by customer segment — is the revenue drop from premium or economy customers?",
        args_schema=CustomerSegmentBreakdownArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_orders_by_status,
        name="get_orders_by_status",
        description="Return order count and value grouped by status — high cancellation rate is a key demand signal.",
        args_schema=OrdersByStatusArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_campaign_revenue_attribution,
        name="get_campaign_revenue_attribution",
        description="Return revenue split between campaign-promoted and organic orders — what share came from campaigns?",
        args_schema=CampaignRevenueAttributionArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_hourly_sales_trend,
        name="get_hourly_sales_trend",
        description="Return order count and revenue per UTC hour for a date (YYYY-MM-DD) — pinpoints the exact hour a drop started.",
        args_schema=HourlySalesTrendArgs,
    ),
    # Inventory
    StructuredTool.from_function(
        coroutine=analyze_inventory,
        name="analyze_inventory",
        description="Snapshot of total inventory value, low-stock count, stockout count, and the list of low-stock products.",
        args_schema=AnalyzeInventoryArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_stock_level,
        name="get_stock_level",
        description="Get current inventory snapshot for a single SKU.",
        args_schema=StockLevelArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_stockout_history,
        name="get_stockout_history",
        description="Return movement events where a SKU hit zero stock within a period.",
        args_schema=StockoutHistoryArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_inventory_turnover,
        name="get_inventory_turnover",
        description="Compute units sold vs quantity on hand (turnover ratio) per SKU for a period.",
        args_schema=InventoryTurnoverArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_revenue_lost_to_stockouts,
        name="get_revenue_lost_to_stockouts",
        description="Estimate revenue lost per SKU due to stockout events in a period.",
        args_schema=RevenueLostToStockoutsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_restock_history,
        name="get_restock_history",
        description="Return all restock events for a SKU in the period — quantity added, timing, and source.",
        args_schema=RestockHistoryArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_products_near_reorder_point,
        name="get_products_near_reorder_point",
        description="Return products above reorder point but within buffer_pct% of it — proactive warning before stockout.",
        args_schema=ProductsNearReorderArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_slow_moving_products,
        name="get_slow_moving_products",
        description="Return in-stock products with low turnover — capital tied up in excess inventory.",
        args_schema=SlowMovingProductsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_inventory_movements,
        name="get_inventory_movements",
        description="Return every inventory movement (sale, restock, adjustment) for a SKU in the period — full audit trail.",
        args_schema=InventoryMovementsArgs,
    ),
    # Marketing
    StructuredTool.from_function(
        coroutine=analyze_marketing,
        name="analyze_marketing",
        description="Aggregate campaign spend, revenue, impressions, clicks, conversions, ROAS, and CTR for a period.",
        args_schema=AnalyzeMarketingArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_underperforming_campaigns,
        name="get_underperforming_campaigns",
        description="List campaigns whose ROAS is below a threshold.",
        args_schema=UnderperformingCampaignArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_campaigns_for_sku,
        name="get_campaigns_for_sku",
        description="Find active and paused campaigns targeting a SKU.",
        args_schema=CampaignsForSkuArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_unpromoted_top_products,
        name="get_unpromoted_top_products",
        description="Find high-revenue products that have no active or paused campaign.",
        args_schema=UnpromotedTopProductsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_campaign_by_channel,
        name="get_campaign_by_channel",
        description="Return aggregated campaign metrics (spend, revenue, ROAS) grouped by channel — identify which channel is failing.",
        args_schema=CampaignByChannelArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_campaigns_near_budget_exhaustion,
        name="get_campaigns_near_budget_exhaustion",
        description="Return active campaigns that have spent >= pct_threshold% of their budget — about to stop running silently.",
        args_schema=CampaignsNearBudgetExhaustionArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_campaign_daily_trend,
        name="get_campaign_daily_trend",
        description="Return day-by-day metrics for a specific campaign — spot acceleration or deceleration.",
        args_schema=CampaignDailyTrendArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_discount_impact,
        name="get_discount_impact",
        description="Return revenue split (campaign-promoted vs organic) plus total discounts given — lift vs margin erosion.",
        args_schema=DiscountImpactArgs,
    ),
    # Support
    StructuredTool.from_function(
        coroutine=analyze_support,
        name="analyze_support",
        description="Combined support health: ticket count, sentiment, negative tickets, and refund rate.",
        args_schema=AnalyzeSupportArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_products_with_high_complaint_rate,
        name="get_products_with_high_complaint_rate",
        description="List products with elevated complaint counts and complaint rate per unit sold.",
        args_schema=HighComplaintRateArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_common_complaint_categories,
        name="get_common_complaint_categories",
        description="Group support tickets by category showing volume and negative ratio.",
        args_schema=ComplaintCategoriesArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_common_return_reasons,
        name="get_common_return_reasons",
        description="Rank return reasons by frequency and total refund amount.",
        args_schema=ReturnReasonsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_churn_risk_products,
        name="get_churn_risk_products",
        description="Find products at churn risk due to poor sentiment or high return counts.",
        args_schema=ChurnRiskProductsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_ticket_resolution_times,
        name="get_ticket_resolution_times",
        description="Return average and p95 ticket resolution time in hours by priority — SLA compliance signal.",
        args_schema=TicketResolutionTimesArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_open_tickets_snapshot,
        name="get_open_tickets_snapshot",
        description="Return current open ticket backlog by priority and category — live queue state with oldest ticket age.",
        args_schema=OpenTicketsSnapshotArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_sentiment_trend,
        name="get_sentiment_trend",
        description="Return daily average sentiment score over the period — is customer mood worsening?",
        args_schema=SentimentTrendArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_return_rate_by_sku,
        name="get_return_rate_by_sku",
        description="Return rate, refund amount, and top return reasons for a specific SKU.",
        args_schema=ReturnRateBySkuArgs,
    ),
    # Shared catalog lookup (available to all domain agents)
    StructuredTool.from_function(
        coroutine=get_product_details,
        name="get_product_details",
        description="Return product metadata (name, category, brand, unit price, cost price) for a SKU.",
        args_schema=ProductDetailsArgs,
    ),
]


ACTION_TOOLS = [
    StructuredTool.from_function(
        coroutine=_adapter_create_purchase_order,
        name="create_purchase_order",
        description="Increase inventory availability for a SKU by adding stock.",
        args_schema=CreatePurchaseOrderArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_suspend_campaign,
        name="suspend_campaign",
        description="Suspend an active campaign to stop spend and impressions.",
        args_schema=SuspendCampaignArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_resume_campaign,
        name="resume_campaign",
        description="Resume a suspended campaign to restore reach and conversions.",
        args_schema=ResumeCampaignArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_create_discount_offer,
        name="create_discount_offer",
        description="Create a percentage discount offer for a SKU to improve conversion.",
        args_schema=CreateDiscountOfferArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_open_customer_issue,
        name="open_customer_issue",
        description="Open a support ticket to track and resolve a customer or product issue.",
        args_schema=OpenCustomerIssueArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_notify_stakeholders,
        name="notify_stakeholders",
        description="Send an alert to stakeholders via a specified channel and record it.",
        args_schema=NotifyStakeholdersArgs,
    ),
]


ALL_TOOLS = [*READ_TOOLS, *ACTION_TOOLS]


# ── Permissioned request adapters ─────────────────────────────────────────
# These tools do NOT execute any action. They build and return an ActionRequest
# dict. Domain agents call them to "request" an action — the infrastructure
# intercepts the tool response, extracts the ActionRequest, and queues it for
# HITL approval. The agent sees a success response and finishes normally.


def _make_request(
    domain: str,
    target: str,
    parameters: dict,
    risk_level: str,
    justification: str,
    estimated_impact: str,
) -> dict:
    """Build a serializable ActionRequest dict."""
    return ActionRequest(
        action_id=str(uuid.uuid4()),
        domain=domain,
        target=target,
        parameters=parameters,  # type: ignore[arg-type]
        risk_level=risk_level,  # type: ignore[arg-type]
        justification=justification,
        estimated_impact=estimated_impact,
    ).model_dump(mode="json")


async def _req_restock(
    sku: str,
    quantity: int,
    justification: str,
    estimated_impact: str = "",
    risk_level: str = "medium",
) -> dict:
    return _make_request(
        domain="inventory",
        target=sku,
        parameters={"action_type": "restock_product", "sku": sku, "quantity": quantity},
        risk_level=risk_level,
        justification=justification,
        estimated_impact=estimated_impact or f"Add {quantity} units to {sku} stock.",
    )


async def _req_resume_campaign(
    campaign_id: str,
    justification: str,
    estimated_impact: str = "",
) -> dict:
    return _make_request(
        domain="marketing",
        target=campaign_id,
        parameters={"action_type": "resume_campaign", "campaign_id": campaign_id},
        risk_level="low",
        justification=justification,
        estimated_impact=estimated_impact or f"Resume reach and conversions for {campaign_id}.",
    )


async def _req_suspend_campaign(
    campaign_id: str,
    justification: str,
    estimated_impact: str = "",
) -> dict:
    return _make_request(
        domain="marketing",
        target=campaign_id,
        parameters={"action_type": "suspend_campaign", "campaign_id": campaign_id},
        risk_level="medium",
        justification=justification,
        estimated_impact=estimated_impact or f"Stop spend for campaign {campaign_id}.",
    )


async def _req_support_ticket(
    subject: str,
    priority: str,
    target: str,
    justification: str,
    estimated_impact: str = "",
) -> dict:
    return _make_request(
        domain="support",
        target=target,
        parameters={
            "action_type": "create_support_ticket",
            "subject": subject,
            "priority": priority,
        },
        risk_level="low",
        justification=justification,
        estimated_impact=estimated_impact or "Create a support ticket for resolution.",
    )


async def _req_alert(
    channel: str,
    message: str,
    target: str,
    justification: str,
    estimated_impact: str = "",
) -> dict:
    return _make_request(
        domain="support",
        target=target,
        parameters={"action_type": "send_alert", "channel": channel, "message": message},
        risk_level="low",
        justification=justification,
        estimated_impact=estimated_impact or f"Deliver alert to {channel}.",
    )


class ReqRestockArgs(BaseModel):
    sku: str = Field(description="SKU to restock")
    quantity: int = Field(gt=0, description="Number of units to request")
    justification: str = Field(description="Business reason for the restock")
    estimated_impact: str = Field(default="", description="Expected business outcome")
    risk_level: str = Field(default="medium", description="low | medium | high")


class ReqResumeCampaignArgs(BaseModel):
    campaign_id: str = Field(description="Campaign ID to resume")
    justification: str = Field(description="Business reason for resuming")
    estimated_impact: str = Field(default="", description="Expected business outcome")


class ReqSuspendCampaignArgs(BaseModel):
    campaign_id: str = Field(description="Campaign ID to suspend")
    justification: str = Field(description="Business reason for suspending")
    estimated_impact: str = Field(default="", description="Expected business outcome")


class ReqSupportTicketArgs(BaseModel):
    subject: str = Field(description="Short description of the issue")
    priority: str = Field(description="Ticket priority: low, medium, or high")
    target: str = Field(description="SKU, campaign, or product name this ticket is for")
    justification: str = Field(description="Why this ticket is needed")
    estimated_impact: str = Field(default="", description="Expected outcome")


class ReqAlertArgs(BaseModel):
    channel: str = Field(description="Notification channel (slack, email, pagerduty)")
    message: str = Field(description="Alert message body")
    target: str = Field(description="What the alert is about")
    justification: str = Field(description="Why this alert is needed")
    estimated_impact: str = Field(default="", description="Expected outcome")


# Names of permissioned tools — used by domain agent nodes to identify
# ActionRequest tool responses in the message history.
PERMISSIONED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "request_restock",
        "request_resume_campaign",
        "request_suspend_campaign",
        "request_support_ticket",
        "request_alert",
    }
)

REQUEST_TOOLS = [
    StructuredTool.from_function(
        coroutine=_req_restock,
        name="request_restock",
        description=(
            "Request a purchase order to restock a SKU. Does NOT execute immediately — "
            "queues the action for human approval. Use when inventory data confirms a stockout "
            "or critical low-stock situation."
        ),
        args_schema=ReqRestockArgs,
    ),
    StructuredTool.from_function(
        coroutine=_req_resume_campaign,
        name="request_resume_campaign",
        description=(
            "Request resumption of a paused campaign. Does NOT execute immediately — "
            "queues for human approval. Use when campaign data shows it was paused "
            "during an active sales period."
        ),
        args_schema=ReqResumeCampaignArgs,
    ),
    StructuredTool.from_function(
        coroutine=_req_suspend_campaign,
        name="request_suspend_campaign",
        description=(
            "Request suspension of an active campaign. Does NOT execute immediately — "
            "queues for human approval. Use when ROAS is confirmed below 1.0."
        ),
        args_schema=ReqSuspendCampaignArgs,
    ),
    StructuredTool.from_function(
        coroutine=_req_support_ticket,
        name="request_support_ticket",
        description=(
            "Request creation of a support ticket. Does NOT execute immediately — "
            "queues for human approval. Use when complaint rate or negative sentiment "
            "is confirmed high for a product."
        ),
        args_schema=ReqSupportTicketArgs,
    ),
    StructuredTool.from_function(
        coroutine=_req_alert,
        name="request_alert",
        description=(
            "Request a stakeholder alert. Does NOT execute immediately — "
            "queues for human approval. Use for critical operational events."
        ),
        args_schema=ReqAlertArgs,
    ),
]
