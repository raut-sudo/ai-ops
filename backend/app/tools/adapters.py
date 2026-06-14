from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.schemas import (
    ActionProposal,
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
    get_inventory_turnover,
    get_revenue_lost_to_stockouts,
    get_stock_level,
    get_stockout_history,
)
from app.tools.marketing import (
    analyze_marketing,
    get_campaigns_for_sku,
    get_underperforming_campaigns,
    get_unpromoted_top_products,
)
from app.tools.sales import (
    analyze_sales,
    get_declining_products,
    get_sales_distribution,
    get_top_products,
)
from app.tools.support import (
    analyze_support,
    get_churn_risk_products,
    get_common_complaint_categories,
    get_common_return_reasons,
    get_products_with_high_complaint_rate,
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
        target=sku,
        parameters=RestockParams(sku=sku, quantity=quantity),
        risk_level="medium",
        justification="Increase inventory availability for the requested SKU.",
        estimated_impact=f"Add {quantity} units to {sku} stock.",
    )
    return await create_purchase_order(proposal)


async def _adapter_suspend_campaign(campaign_id: str):
    proposal = ActionProposal(
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
        target=sku,
        parameters=DiscountParams(sku=sku, percent=percent),
        risk_level="medium",
        justification="Create a discount offer to improve conversion for the SKU.",
        estimated_impact=f"Apply {percent}% discount to {sku}.",
    )
    return await create_discount_offer(proposal)


async def _adapter_open_customer_issue(subject: str, priority: str):
    proposal = ActionProposal(
        target="support",
        parameters=TicketParams(subject=subject, priority=priority),
        risk_level="low",
        justification="Open a customer issue for follow-up.",
        estimated_impact="Create a support ticket for resolution.",
    )
    return await open_customer_issue(proposal)


async def _adapter_notify_stakeholders(channel: str, message: str):
    proposal = ActionProposal(
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
