"""Intent classifier node.

Primary path uses structured LLM output. A deterministic fallback keeps tests
and local development functional without external model credentials.
"""

from __future__ import annotations

import re
from importlib import import_module

from langchain_core.messages import AnyMessage, SystemMessage

from app.config import settings
from app.graph.state import AgentState
from app.schemas import IntentClassification

INTENT_SYSTEM_PROMPT = """
You are an intent classification engine for an AI-powered E-Commerce Operations Brain.

Classify the user's query and determine which business domains need investigation.

INTENT TYPES:
- business_diagnosis
- inventory_check
- marketing_analysis
- support_analysis
- cross_domain_analysis
- memory_recall
- direct_action
- reporting
- irrelevant

DOMAINS: sales, inventory, marketing, support

Rules:
- Use memory_needed=true for why/again/previous/before/recurrence/cross-domain queries.
- action_only=true only for direct_action.
- required_domains must only contain valid domains.
- reasoning must be one sentence.

Return JSON matching IntentClassification exactly.
"""


def _conversation_text(
    query: str,
    history: list[AnyMessage] | None = None,
) -> str:
    """Build a lightweight conversation context."""

    parts: list[str] = []

    if history:
        # last few turns are enough
        for msg in history[-8:]:
            content = getattr(msg, "content", "")
            if isinstance(content, str) and content.strip():
                parts.append(content.lower())

    parts.append(query.lower())

    return "\n".join(parts)


def _keyword_intent(
    query: str,
    history: list[AnyMessage] | None = None,
) -> IntentClassification:
    current_q = query.lower()
    q = _conversation_text(query, history)

    inventory_words = {"inventory", "stock", "stockout", "oos", "restock", "sku"}
    marketing_words = {"campaign", "roas", "spend", "ads", "marketing"}
    support_words = {"ticket", "complaint", "refund", "support", "return"}
    sales_words = {"sales", "revenue", "orders", "aov", "units sold", "drop"}

    has_inventory = any(w in q for w in inventory_words) or bool(re.search(r"sku[-_ ]?\d+", q))
    has_marketing = any(w in q for w in marketing_words)
    has_support = any(w in q for w in support_words)
    has_sales = any(w in q for w in sales_words)

    follow_up_tokens = {
        "same",
        "also",
        "again",
        "still",
        "what about",
        "how about",
        "it",
        "that",
        "those",
    }

    is_follow_up = any(token in current_q for token in follow_up_tokens)

    memory_needed = (
        any(
            token in q
            for token in [
                "why",
                "again",
                "still",
                "keeps",
                "before",
                "previous",
                "last time",
            ]
        )
        or is_follow_up
    )

    # irrelevant uses only current turn
    if any(w in current_q for w in ["weather", "movie", "football", "recipe", "politics"]):
        return IntentClassification(
            intent_type="irrelevant",
            required_domains=[],
            memory_needed=False,
            action_only=False,
            confidence=0.95,
            reasoning="The query is unrelated to e-commerce operations.",
        )

    # memory recall
    if any(
        w in current_q
        for w in [
            "what happened last time",
            "have we seen",
            "past incident",
            "previous incident",
        ]
    ):
        return IntentClassification(
            intent_type="memory_recall",
            required_domains=[],
            memory_needed=True,
            action_only=False,
            confidence=0.88,
            reasoning="The user is requesting historical memory.",
        )

    # direct actions should only depend on the current turn
    if any(
        w in current_q
        for w in [
            "pause",
            "activate",
            "restock",
            "apply discount",
            "send alert",
            "create ticket",
        ]
    ):
        return IntentClassification(
            intent_type="direct_action",
            required_domains=[],
            memory_needed=False,
            action_only=True,
            confidence=0.9,
            reasoning="The user is requesting an operational action.",
        )

    domains: list[str] = []

    if has_sales:
        domains.append("sales")

    if has_inventory:
        domains.append("inventory")

    if has_marketing:
        domains.append("marketing")

    if has_support:
        domains.append("support")

    if len(domains) > 1:
        return IntentClassification(
            intent_type="cross_domain_analysis",
            required_domains=domains,
            memory_needed=True,
            action_only=False,
            confidence=0.86,
            reasoning="Multiple domains require correlation.",
        )

    if domains == ["inventory"]:
        intent_type = "inventory_check"

    elif domains == ["marketing"]:
        intent_type = "marketing_analysis"

    elif domains == ["support"]:
        intent_type = "support_analysis"

    elif domains == ["sales"]:
        intent_type = "business_diagnosis" if ("why" in q or "drop" in q) else "reporting"

    else:
        intent_type = "business_diagnosis"
        domains = ["sales", "inventory"]

    return IntentClassification(
        intent_type=intent_type,
        required_domains=domains,
        memory_needed=memory_needed
        or intent_type
        in {
            "business_diagnosis",
            "cross_domain_analysis",
        },
        action_only=False,
        confidence=0.78,
        reasoning="The query requires operational analysis.",
    )


async def _llm_classify(query: str, history: list) -> IntentClassification | None:
    if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
        return None

    try:
        AzureChatOpenAI = import_module("langchain_openai").AzureChatOpenAI
    except Exception:
        return None

    llm = AzureChatOpenAI(
        azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version=settings.AZURE_OPENAI_API_VERSION,
        azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O_MINI,
        temperature=0,
    ).with_structured_output(IntentClassification)

    # System prompt + full conversation history gives the classifier context
    # about what the user has been discussing, enabling accurate intent
    # routing on follow-up turns (e.g. "do the same for inventory").
    messages = [SystemMessage(content=INTENT_SYSTEM_PROMPT), *history]

    try:
        return await llm.ainvoke(messages)
    except Exception:
        return None


async def intent_classifier_node(state: AgentState) -> dict:
    """Classify query intent and initialize retry_count."""
    # existing_intent = state.get("intent")
    # if existing_intent is not None:
    #     return {"intent": existing_intent, "retry_count": 0}

    query = state["query"]
    history = state.get("messages", [])
    result = await _llm_classify(query, history)
    if result is None:
        result = _keyword_intent(query=query, history=history)

    return {
        "intent": result,
        "retry_count": 0,
    }
