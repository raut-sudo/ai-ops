"""Unit tests for routing edges.

Per BLUEPRINT §24.1: edge tests build AgentState by hand and never depend on
stub returns. This tests routing logic independently of node behavior.
"""

from __future__ import annotations

from datetime import UTC

from app.graph.edges import (
    route_after_action_agent,
    route_after_hitl,
    route_after_intent,
    route_after_reflection,
)
from app.schemas import (
    ActionProposal,
    HITLDecision,
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

    def test_irrelevant_intent_goes_to_assemble(self):
        """Irrelevant intent → assemble_response (skip investigation)."""
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
        assert result == "assemble_response"

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

    def test_direct_action_goes_to_action_agent(self):
        """Direct action intent → action_agent (no diagnosis)."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="direct_action",
            reasoning="User asking for immediate action.",
            required_domains=[],
            action_only=True,
            memory_needed=False,
            confidence=0.85,
        )

        result = route_after_intent(state)
        assert result == "action_agent"

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


class TestRouteAfterReflection:
    """Test routing after reflection (retry decision point)."""

    def test_retry_with_domains_below_max_retries(self):
        """verdict=retry_with_domains, retry_count < MAX → fan_out (targeted)."""
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

    def test_retry_at_max_retries_goes_to_assemble(self):
        """retry_with_domains but retry_count >= MAX_RETRIES → assemble_response."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="business_diagnosis",
            reasoning="Initial.",
            required_domains=["sales"],
            action_only=False,
            memory_needed=False,
            confidence=0.7,
        )
        state["retry_count"] = 3  # MAX_RETRIES = 3
        state["reflection_result"] = ReflectionResult(
            verdict="retry_with_domains",
            critique="Would retry but hit limit.",
            domains_to_retry=["sales"],
            confidence=0.6,
        )

        result = route_after_reflection(state)
        assert result == "assemble_response"

    def test_actionable_intent_with_root_causes_goes_to_action_agent(self):
        """Actionable intent + root_causes → action_agent."""
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
                RootCause(
                    cause="Campaign paused",
                    domain="marketing",
                    evidence=["Summer Sale status=paused"],
                    confidence=0.85,
                ),
            ],
            contributing_factors={"inventory": "Out of stock", "marketing": "Campaign paused"},
            confidence_score=0.85,
            recommendations=["Restock SKU-101", "Reactivate campaign"],
            domains_correlated=["inventory", "marketing"],
        )
        state["reflection_result"] = ReflectionResult(
            verdict="pass",
            critique="Ready for action.",
            domains_to_retry=[],
            confidence=0.9,
        )

        result = route_after_reflection(state)
        assert result == "action_agent"

    def test_actionable_intent_without_root_causes_goes_to_assemble(self):
        """Actionable intent but no root_causes → assemble_response."""
        state = _base_state()
        state["intent"] = IntentClassification(
            intent_type="business_diagnosis",
            reasoning="No root cause found.",
            required_domains=["sales"],
            action_only=False,
            memory_needed=False,
            confidence=0.5,
        )
        state["synthesis"] = SynthesisResult(
            correlated_explanation="No issues detected.",
            root_causes=[],
            contributing_factors={},
            confidence_score=0.5,
            recommendations=[],
            domains_correlated=[],
        )
        state["reflection_result"] = ReflectionResult(
            verdict="pass",
            critique="Nothing to act on.",
            domains_to_retry=[],
            confidence=0.7,
        )

        result = route_after_reflection(state)
        assert result == "assemble_response"

    def test_non_actionable_intent_goes_to_assemble(self):
        """Non-actionable intent (e.g., reporting) → assemble_response."""
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
            critique="Ready to assemble.",
            domains_to_retry=[],
            confidence=0.8,
        )

        result = route_after_reflection(state)
        assert result == "assemble_response"


class TestRouteAfterActionAgent:
    """Test routing after action agent (proposal decision)."""

    def test_proposals_exist_goes_to_hitl_node(self):
        """proposed_actions non-empty → hitl_node (interrupt)."""
        state = _base_state()
        state["proposed_actions"] = [
            ActionProposal(
                action_id="act-001",
                target="inventory",
                parameters={"action_type": "restock_product", "sku": "SKU-101", "quantity": 100},
                risk_level="low",
                justification="Restock to recover sales.",
                estimated_impact="Increase inventory availability.",
            )
        ]

        result = route_after_action_agent(state)
        assert result == "hitl_node"

    def test_no_proposals_goes_to_assemble(self):
        """proposed_actions empty → assemble_response."""
        state = _base_state()
        state["proposed_actions"] = []

        result = route_after_action_agent(state)
        assert result == "assemble_response"


class TestRouteAfterHitl:
    """Test routing after HITL (approval decision)."""

    def test_approved_actions_go_to_execute_actions(self):
        """approved_action_ids non-empty → execute_actions."""
        state = _base_state()
        state["proposed_actions"] = [
            ActionProposal(
                action_id="act-001",
                target="inventory",
                parameters={"action_type": "restock_product", "sku": "SKU-101", "quantity": 100},
                risk_level="low",
                justification="Restock.",
                estimated_impact="Increase stock.",
            )
        ]
        state["hitl_decision"] = HITLDecision(
            approved_action_ids=["act-001"],
            rejected_action_ids=[],
            approver="user-123",
        )

        result = route_after_hitl(state)
        assert result == "execute_actions"

    def test_no_hitl_decision_goes_to_assemble(self):
        """hitl_decision=None → assemble_response."""
        state = _base_state()
        state["hitl_decision"] = None

        result = route_after_hitl(state)
        assert result == "assemble_response"

    def test_rejected_actions_go_to_assemble(self):
        """approved_action_ids empty but hitl_decision exists → assemble_response."""
        state = _base_state()
        state["hitl_decision"] = HITLDecision(
            approved_action_ids=[],
            rejected_action_ids=["act-001"],
            approver="user-123",
            rejection_reason="Too risky",
        )

        result = route_after_hitl(state)
        assert result == "assemble_response"
