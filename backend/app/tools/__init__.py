from app.tools.actions import (
    ACTION_DISPATCH,
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

__all__ = [
    "ACTION_DISPATCH",
    "analyze_inventory",
    "analyze_marketing",
    "analyze_sales",
    "analyze_support",
    "create_discount_offer",
    "create_purchase_order",
    "get_campaigns_for_sku",
    "get_churn_risk_products",
    "get_common_complaint_categories",
    "get_common_return_reasons",
    "get_declining_products",
    "get_inventory_turnover",
    "get_products_with_high_complaint_rate",
    "get_revenue_lost_to_stockouts",
    "get_sales_distribution",
    "get_stock_level",
    "get_stockout_history",
    "get_top_products",
    "get_underperforming_campaigns",
    "get_unpromoted_top_products",
    "notify_stakeholders",
    "open_customer_issue",
    "resume_campaign",
    "suspend_campaign",
]
