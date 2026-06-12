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
    activate_campaign,
    apply_discount,
    create_support_ticket,
    pause_campaign,
    restock_product,
    send_alert,
)
from app.tools.inventory import (
    get_low_stock_products,
    get_stock_level,
    get_stockout_history,
    was_out_of_stock,
)
from app.tools.marketing import (
    get_active_campaigns_for_sku,
    get_campaign_performance,
    get_underperforming_campaigns,
)
from app.tools.sales import (
    compare_sales_periods,
    get_sales_by_region,
    get_sales_metrics,
    get_top_products,
)
from app.tools.support import (
    get_complaints_by_sku,
    get_refund_rate,
    get_support_metrics,
    get_ticket_trends,
)


class SalesMetricsArgs(BaseModel):
    period: str = Field(description="Time window (yesterday, last_7_days, last_30_days)")
    region: str | None = Field(default=None)
    sku: str | None = Field(default=None)


class CompareSalesArgs(BaseModel):
    period_a: str
    period_b: str


class TopProductsArgs(BaseModel):
    period: str
    limit: int = Field(default=10, ge=1, le=100)


class RegionSalesArgs(BaseModel):
    period: str


class StockLevelArgs(BaseModel):
    sku: str


class OutOfStockArgs(BaseModel):
    sku: str
    timestamp: str = Field(description="ISO timestamp")


class StockoutHistoryArgs(BaseModel):
    sku: str
    period: str


class CampaignPerformanceArgs(BaseModel):
    period: str
    campaign_id: str | None = None


class UnderperformingCampaignArgs(BaseModel):
    period: str
    roas_threshold: float = 1.0


class CampaignForSkuArgs(BaseModel):
    sku: str


class SupportMetricsArgs(BaseModel):
    period: str


class ComplaintsBySkuArgs(BaseModel):
    sku: str
    period: str


class RefundRateArgs(BaseModel):
    period: str


class TicketTrendsArgs(BaseModel):
    period: str


class RestockActionArgs(BaseModel):
    action_id: str
    target: str
    sku: str
    quantity: int = Field(gt=0)
    risk_level: str = "medium"
    justification: str = "Auto-generated restock action"
    estimated_impact: str = "Increase stock availability"


class PauseCampaignActionArgs(BaseModel):
    action_id: str
    target: str
    campaign_id: str
    risk_level: str = "medium"
    justification: str = "Auto-generated pause campaign action"
    estimated_impact: str = "Pause campaign to reduce spend"


class ActivateCampaignActionArgs(BaseModel):
    action_id: str
    target: str
    campaign_id: str
    risk_level: str = "medium"
    justification: str = "Auto-generated activate campaign action"
    estimated_impact: str = "Activate campaign to increase reach"


class DiscountActionArgs(BaseModel):
    action_id: str
    target: str
    sku: str
    percent: float = Field(gt=0, le=90)
    risk_level: str = "medium"
    justification: str = "Auto-generated discount action"
    estimated_impact: str = "Increase conversion"


class TicketActionArgs(BaseModel):
    action_id: str
    target: str
    subject: str
    priority: str = Field(description="low, medium, high")
    risk_level: str = "low"
    justification: str = "Auto-generated support action"
    estimated_impact: str = "Create support follow-up"


class AlertActionArgs(BaseModel):
    action_id: str
    target: str
    channel: str
    message: str
    risk_level: str = "low"
    justification: str = "Auto-generated alert action"
    estimated_impact: str = "Notify stakeholders"


async def _adapter_was_out_of_stock(sku: str, timestamp: str) -> bool:
    from datetime import datetime

    return await was_out_of_stock(sku=sku, timestamp=datetime.fromisoformat(timestamp))


async def _adapter_restock_product(**kwargs):
    proposal = ActionProposal(
        action_id=kwargs["action_id"],
        target=kwargs["target"],
        parameters=RestockParams(sku=kwargs["sku"], quantity=kwargs["quantity"]),
        risk_level=kwargs["risk_level"],
        justification=kwargs["justification"],
        estimated_impact=kwargs["estimated_impact"],
    )
    return await restock_product(proposal)


async def _adapter_pause_campaign(**kwargs):
    proposal = ActionProposal(
        action_id=kwargs["action_id"],
        target=kwargs["target"],
        parameters=CampaignParams(
            action_type="pause_campaign",
            campaign_id=kwargs["campaign_id"],
        ),
        risk_level=kwargs["risk_level"],
        justification=kwargs["justification"],
        estimated_impact=kwargs["estimated_impact"],
    )
    return await pause_campaign(proposal)


async def _adapter_activate_campaign(**kwargs):
    proposal = ActionProposal(
        action_id=kwargs["action_id"],
        target=kwargs["target"],
        parameters=CampaignParams(
            action_type="activate_campaign",
            campaign_id=kwargs["campaign_id"],
        ),
        risk_level=kwargs["risk_level"],
        justification=kwargs["justification"],
        estimated_impact=kwargs["estimated_impact"],
    )
    return await activate_campaign(proposal)


async def _adapter_apply_discount(**kwargs):
    proposal = ActionProposal(
        action_id=kwargs["action_id"],
        target=kwargs["target"],
        parameters=DiscountParams(sku=kwargs["sku"], percent=kwargs["percent"]),
        risk_level=kwargs["risk_level"],
        justification=kwargs["justification"],
        estimated_impact=kwargs["estimated_impact"],
    )
    return await apply_discount(proposal)


async def _adapter_create_support_ticket(**kwargs):
    proposal = ActionProposal(
        action_id=kwargs["action_id"],
        target=kwargs["target"],
        parameters=TicketParams(subject=kwargs["subject"], priority=kwargs["priority"]),
        risk_level=kwargs["risk_level"],
        justification=kwargs["justification"],
        estimated_impact=kwargs["estimated_impact"],
    )
    return await create_support_ticket(proposal)


async def _adapter_send_alert(**kwargs):
    proposal = ActionProposal(
        action_id=kwargs["action_id"],
        target=kwargs["target"],
        parameters=AlertParams(channel=kwargs["channel"], message=kwargs["message"]),
        risk_level=kwargs["risk_level"],
        justification=kwargs["justification"],
        estimated_impact=kwargs["estimated_impact"],
    )
    return await send_alert(proposal)


READ_TOOLS = [
    StructuredTool.from_function(
        coroutine=get_sales_metrics,
        name="get_sales_metrics",
        description="Aggregate revenue, order count, AOV, and units sold for a period.",
        args_schema=SalesMetricsArgs,
    ),
    StructuredTool.from_function(
        coroutine=compare_sales_periods,
        name="compare_sales_periods",
        description="Compare sales metrics between two semantic periods.",
        args_schema=CompareSalesArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_top_products,
        name="get_top_products",
        description="Get top-selling products for a period.",
        args_schema=TopProductsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_sales_by_region,
        name="get_sales_by_region",
        description="Get sales metrics grouped by region for a period.",
        args_schema=RegionSalesArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_stock_level,
        name="get_stock_level",
        description="Get inventory snapshot for a SKU.",
        args_schema=StockLevelArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_low_stock_products,
        name="get_low_stock_products",
        description="List products at/below reorder point.",
    ),
    StructuredTool.from_function(
        coroutine=_adapter_was_out_of_stock,
        name="was_out_of_stock",
        description="Check whether a SKU was out of stock at a specific timestamp.",
        args_schema=OutOfStockArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_stockout_history,
        name="get_stockout_history",
        description="Return stockout events for a SKU over a period.",
        args_schema=StockoutHistoryArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_campaign_performance,
        name="get_campaign_performance",
        description="Summarize campaign performance and ROAS for a period.",
        args_schema=CampaignPerformanceArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_underperforming_campaigns,
        name="get_underperforming_campaigns",
        description="List campaigns with ROAS below threshold.",
        args_schema=UnderperformingCampaignArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_active_campaigns_for_sku,
        name="get_active_campaigns_for_sku",
        description="Find active/paused campaigns targeting a SKU.",
        args_schema=CampaignForSkuArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_support_metrics,
        name="get_support_metrics",
        description="Get ticket volume and sentiment metrics for a period.",
        args_schema=SupportMetricsArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_complaints_by_sku,
        name="get_complaints_by_sku",
        description="Get negative-sentiment complaints for a SKU in a period.",
        args_schema=ComplaintsBySkuArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_refund_rate,
        name="get_refund_rate",
        description="Compute refund rate for a period.",
        args_schema=RefundRateArgs,
    ),
    StructuredTool.from_function(
        coroutine=get_ticket_trends,
        name="get_ticket_trends",
        description="Summarize support trends by category.",
        args_schema=TicketTrendsArgs,
    ),
]


ACTION_TOOLS = [
    StructuredTool.from_function(
        coroutine=_adapter_restock_product,
        name="restock_product",
        description="Restock a SKU and write an inventory movement entry.",
        args_schema=RestockActionArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_pause_campaign,
        name="pause_campaign",
        description="Pause a campaign to reduce spend and impressions.",
        args_schema=PauseCampaignActionArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_activate_campaign,
        name="activate_campaign",
        description="Activate a campaign to increase reach and conversions.",
        args_schema=ActivateCampaignActionArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_apply_discount,
        name="apply_discount",
        description="Create an auto campaign discount for a SKU.",
        args_schema=DiscountActionArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_create_support_ticket,
        name="create_support_ticket",
        description="Create a support ticket.",
        args_schema=TicketActionArgs,
    ),
    StructuredTool.from_function(
        coroutine=_adapter_send_alert,
        name="send_alert",
        description="Emit an alert event into audit logs.",
        args_schema=AlertActionArgs,
    ),
]


ALL_TOOLS = [*READ_TOOLS, *ACTION_TOOLS]
