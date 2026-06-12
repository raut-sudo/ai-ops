"""Intent classifier node stub.

Pure LLM structured output — no tools. Returns IntentClassification.
"""

from __future__ import annotations

from app.graph.state import AgentState
from app.schemas import IntentClassification


async def intent_classifier_node(state: AgentState) -> dict:
    """Stub: classify query intent and initialize retry_count."""
    existing_intent = state.get("intent")
    if existing_intent is not None:
        return {"intent": existing_intent, "retry_count": 0}

    # Stub returns: business_diagnosis, required_domains=[sales, inventory], memory_needed=True
    return {
        "intent": IntentClassification(
            intent_type="business_diagnosis",
            reasoning="Placeholder stub intent classification.",
            required_domains=["sales", "inventory"],
            action_only=False,
            memory_needed=True,
            confidence=0.95,
        ),
        "retry_count": 0,
    }
