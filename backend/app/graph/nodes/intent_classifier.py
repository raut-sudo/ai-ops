"""Intent classifier node.

Primary path uses structured LLM output. A deterministic fallback keeps tests
and local development functional without external model credentials.
"""

from __future__ import annotations

import re
from importlib import import_module

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


def _keyword_intent(query: str) -> IntentClassification:
    q = query.lower()

    inventory_words = {"inventory", "stock", "stockout", "oos", "restock", "sku"}
    marketing_words = {"campaign", "roas", "spend", "ads", "marketing"}
    support_words = {"ticket", "complaint", "refund", "support", "return"}
    sales_words = {"sales", "revenue", "orders", "aov", "units sold", "drop"}

    has_inventory = any(w in q for w in inventory_words) or bool(re.search(r"sku[-_ ]?\d+", q))
    has_marketing = any(w in q for w in marketing_words)
    has_support = any(w in q for w in support_words)
    has_sales = any(w in q for w in sales_words)

    memory_needed = any(
        token in q
        for token in ["why", "again", "still", "keeps", "before", "previous", "last time"]
    )

    if any(w in q for w in ["weather", "movie", "football", "recipe", "politics"]):
        return IntentClassification(
            intent_type="irrelevant",
            required_domains=[],
            memory_needed=False,
            action_only=False,
            confidence=0.95,
            reasoning="The query is not related to e-commerce operations.",
        )

    if any(
        w in q
        for w in ["what happened last time", "have we seen", "past incident", "previous incident"]
    ):
        return IntentClassification(
            intent_type="memory_recall",
            required_domains=[],
            memory_needed=True,
            action_only=False,
            confidence=0.88,
            reasoning="The user is requesting historical incident memory.",
        )

    if any(
        w in q
        for w in ["pause", "activate", "restock", "apply discount", "send alert", "create ticket"]
    ):
        return IntentClassification(
            intent_type="direct_action",
            required_domains=[],
            memory_needed=False,
            action_only=True,
            confidence=0.9,
            reasoning="The user is asking for an immediate operational action.",
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
            memory_needed=memory_needed or True,
            action_only=False,
            confidence=0.86,
            reasoning="The query spans multiple domains and needs correlation.",
        )

    if domains == ["inventory"]:
        intent_type = "inventory_check"
    elif domains == ["marketing"]:
        intent_type = "marketing_analysis"
    elif domains == ["support"]:
        intent_type = "support_analysis"
    elif domains == ["sales"]:
        intent_type = "business_diagnosis" if "why" in q or "drop" in q else "reporting"
    else:
        intent_type = "business_diagnosis"
        domains = ["sales", "inventory"]

    return IntentClassification(
        intent_type=intent_type,
        required_domains=domains,
        memory_needed=memory_needed
        or intent_type in {"business_diagnosis", "cross_domain_analysis"},
        action_only=False,
        confidence=0.78,
        reasoning="The query requires operational diagnosis based on detected business terms.",
    )


async def _llm_classify(query: str) -> IntentClassification | None:
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

    try:
        return await llm.ainvoke(
            [
                {"role": "system", "content": INTENT_SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ]
        )
    except Exception:
        return None


async def intent_classifier_node(state: AgentState) -> dict:
    """Classify query intent and initialize retry_count."""
    existing_intent = state.get("intent")
    if existing_intent is not None:
        return {"intent": existing_intent, "retry_count": 0}

    query = state["query"]
    result = await _llm_classify(query)
    if result is None:
        result = _keyword_intent(query)

    return {
        "intent": result,
        "retry_count": 0,
    }
