"""Unit tests for routing edges.

Per BLUEPRINT §24.1: edge tests build AgentState by hand and never depend on
stub returns. This tests routing logic independently of node behavior.
"""

from __future__ import annotations

from datetime import UTC

from app.graph.edges import (
    route_after_intent,
    route_after_reflection,
)
from app.schemas import (
    IntentClassification,
    ReflectionResult,
    RootCause,
    SynthesisResult,
)


def _base_state() -> dict:
    """Factory: minimal valid AgentState."""
    from datetime import datetime

    return {
        "messages": [],
        "query": "Test query",
        "session_id": "test-session",
        "thread_id": "test-thread",
        "user_id": "test-user",
        "intent": None,
        "domain_findings": {},
        "memory_context": None,
        "synthesis": None,
        "reflection_result": None,
        "retry_count": 0,
        "proposed_actions": [],
        "hitl_decision": None,
        "action_results": [],
        "final_response": None,
        "error": None,
        "otel_trace_id": "test-trace",
        "langsmith_run_id": None,
        "created_at": datetime.now(UTC),
    }


class TestRouteAfterIntent:
    """Test routing immediately after intent classification."""

    def test_irrelevant_intent_goes_to_aggregator(self):
        """Irrelevant intent → aggregator (skip investigation)."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="irrelevant",
            reasoning="Query unrelated to e-commerce.",
            required_domains=[],
            action_only=False,
            memory_needed=False,
            confidence=0.95,
        )

        result = route_after_intent(state)
        assert result == "aggregator"

    def test_memory_recall_goes_to_memory_retrieve(self):
        """Memory recall intent → memory_retrieve (skip domain agents)."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="memory_recall",
            reasoning="User asking about past incidents.",
            required_domains=[],
            action_only=False,
            memory_needed=True,
            confidence=0.9,
        )

        result = route_after_intent(state)
        assert result == "memory_retrieve"

    def test_business_diagnosis_fans_out_to_domains(self):
        """Business diagnosis with required_domains → fan_out (parallel)."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="business_diagnosis",
            reasoning="Sales drop diagnosis.",
            required_domains=["sales", "inventory"],
            action_only=False,
            memory_needed=True,
            confidence=0.9,
        )

        result = route_after_intent(state)

        # Result should be a list of Send commands
        assert isinstance(result, list)
        assert len(result) >= 2  # At least sales + inventory + memory

        node_names = [send.node for send in result]
        assert "sales_agent" in node_names
        assert "inventory_agent" in node_names
        assert "memory_retrieve" in node_names  # First pass, memory_needed=True

    def test_fan_out_excludes_memory_on_retry(self):
        """memory_needed=True but retry_count > 0 → memory_retrieve not sent."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="business_diagnosis",
            reasoning="Retry diagnosis.",
            required_domains=["sales"],
            action_only=False,
            memory_needed=True,
            confidence=0.8,
        )
        state["retry_count"] = 1  # Already on retry

        result = route_after_intent(state)

        assert isinstance(result, list)
        node_names = [send.node for send in result]
        assert "sales_agent" in node_names
        assert "memory_retrieve" not in node_names  # Skipped on retry

    def test_no_domains_no_memory_goes_to_aggregator(self):
        """No domains + no memory → aggregator (safe fallback)."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="reporting",
            reasoning="Summary requested with no specific domain.",
            required_domains=[],
            action_only=False,
            memory_needed=False,
            confidence=0.8,
        )

        result = route_after_intent(state)
        assert result == "aggregator"


class TestRouteAfterReflection:
    """Test routing after reflection (retry decision point)."""

    def test_retry_with_domains_below_max_retries(self):
        """verdict=retry_with_domains, retry_count <= MAX → fan_out (targeted)."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="business_diagnosis",
            reasoning="Initial diagnosis.",
            required_domains=["sales", "inventory"],
            action_only=False,
            memory_needed=False,
            confidence=0.8,
        )
        state["retry_count"] = 1
        state["reflection_result"] = ReflectionResult(
            verdict="retry_with_domains",
            critique="Need more inventory data.",
            domains_to_retry=["inventory"],
            confidence=0.75,
        )

        result = route_after_reflection(state)

        assert isinstance(result, list)
        node_names = [send.node for send in result]
        assert "inventory_agent" in node_names
        assert "sales_agent" not in node_names  # Targeted retry

    def test_retry_at_max_retries_goes_to_aggregator(self):
        """retry_with_domains but retry_count > MAX_RETRIES → aggregator."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="business_diagnosis",
            reasoning="Initial.",
            required_domains=["sales"],
            action_only=False,
            memory_needed=False,
            confidence=0.7,
        )
        state["retry_count"] = 4  # Exceeds MAX_RETRIES=3
        state["reflection_result"] = ReflectionResult(
            verdict="retry_with_domains",
            critique="Would retry but hit limit.",
            domains_to_retry=["sales"],
            confidence=0.6,
        )

        result = route_after_reflection(state)
        assert result == "aggregator"

    def test_pass_verdict_goes_to_aggregator(self):
        """pass verdict → aggregator (action proposals are internal to reflection)."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="business_diagnosis",
            reasoning="Diagnosis.",
            required_domains=["sales"],
            action_only=False,
            memory_needed=False,
            confidence=0.9,
        )
        state["synthesis"] = SynthesisResult(
            correlated_explanation="Double hit: SKU-101 out of stock + Campaign paused.",
            root_causes=[
                RootCause(
                    cause="Stockout of best-seller",
                    domain="inventory",
                    evidence=["SKU-101 quantity=0"],
                    confidence=0.9,
                ),
            ],
            contributing_factors={"inventory": "Out of stock"},
            confidence_score=0.85,
            recommendations=["Restock SKU-101"],
            domains_correlated=["inventory"],
        )
        state["reflection_result"] = ReflectionResult(
            verdict="pass",
            critique="Ready for aggregation.",
            domains_to_retry=[],
            confidence=0.9,
        )

        result = route_after_reflection(state)
        assert result == "aggregator"

    def test_fail_verdict_goes_to_aggregator(self):
        """fail verdict → aggregator."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="business_diagnosis",
            reasoning="No root cause found.",
            required_domains=["sales"],
            action_only=False,
            memory_needed=False,
            confidence=0.5,
        )
        state["reflection_result"] = ReflectionResult(
            verdict="fail",
            critique="Cannot determine root cause.",
            domains_to_retry=[],
            confidence=0.3,
        )

        result = route_after_reflection(state)
        assert result == "aggregator"

    def test_pass_no_root_causes_goes_to_aggregator(self):
        """pass verdict with no root causes (lookup result) → aggregator."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="reporting",
            reasoning="Summary requested.",
            required_domains=["sales"],
            action_only=False,
            memory_needed=False,
            confidence=0.8,
        )
        state["reflection_result"] = ReflectionResult(
            verdict="pass",
            critique="Ready to aggregate.",
            domains_to_retry=[],
            confidence=0.8,
        )

        result = route_after_reflection(state)
        assert result == "aggregator"
