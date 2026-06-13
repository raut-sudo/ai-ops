from __future__ import annotations

from app.graph.nodes.execute_actions import ACTION_TYPE_TO_DISPATCH_KEY


def test_adapter_contains_all_action_types() -> None:
    assert ACTION_TYPE_TO_DISPATCH_KEY["restock_product"] == "create_purchase_order"
    assert ACTION_TYPE_TO_DISPATCH_KEY["apply_discount"] == "create_discount_offer"
    assert ACTION_TYPE_TO_DISPATCH_KEY["suspend_campaign"] == "suspend_campaign"
    assert ACTION_TYPE_TO_DISPATCH_KEY["resume_campaign"] == "resume_campaign"
    assert ACTION_TYPE_TO_DISPATCH_KEY["create_support_ticket"] == "open_customer_issue"
    assert ACTION_TYPE_TO_DISPATCH_KEY["send_alert"] == "notify_stakeholders"


def test_adapter_unknown_action_type_returns_none() -> None:
    assert ACTION_TYPE_TO_DISPATCH_KEY.get("nonexistent_action") is None
