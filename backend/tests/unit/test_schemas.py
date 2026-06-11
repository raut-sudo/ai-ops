from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import ActionProposal, DiscountParams, RestockParams


def test_action_params_discriminator_dispatches_restock() -> None:
    proposal = ActionProposal.model_validate(
        {
            "target": "inventory:SKU-101",
            "parameters": {
                "action_type": "restock_product",
                "sku": "SKU-101",
                "quantity": 20,
            },
            "risk_level": "low",
            "justification": "Demand spike",
            "estimated_impact": "Avoid stockout",
        }
    )

    assert isinstance(proposal.parameters, RestockParams)


def test_action_params_discriminator_dispatches_discount() -> None:
    proposal = ActionProposal.model_validate(
        {
            "target": "pricing:SKU-200",
            "parameters": {
                "action_type": "apply_discount",
                "sku": "SKU-200",
                "percent": 10,
            },
            "risk_level": "medium",
            "justification": "Slow movement",
            "estimated_impact": "Increase conversion",
        }
    )

    assert isinstance(proposal.parameters, DiscountParams)


def test_action_type_is_derived_from_parameters() -> None:
    proposal = ActionProposal.model_validate(
        {
            "target": "inventory:SKU-999",
            "parameters": {
                "action_type": "restock_product",
                "sku": "SKU-999",
                "quantity": 5,
            },
            "risk_level": "low",
            "justification": "Prevent OOS",
            "estimated_impact": "Maintain fulfillment",
        }
    )

    assert proposal.action_type == "restock_product"
    assert "action_type" not in proposal.model_dump()


def test_invalid_discriminator_value_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        ActionProposal.model_validate(
            {
                "target": "unknown",
                "parameters": {
                    "action_type": "unknown_action",
                },
                "risk_level": "low",
                "justification": "n/a",
                "estimated_impact": "n/a",
            }
        )
