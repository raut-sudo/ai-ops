"""LangGraph construction: build_graph() + compile_graph().

This is the topology definition before behavior (Phase 4). All nodes are stubs.
All routing edges are correct and testable independently of stub returns.
"""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.graph.edges import (
    route_after_action_agent,
    route_after_hitl,
    route_after_intent,
    route_after_reflection,
)
from app.graph.nodes import (
    action_agent_node,
    assemble_response_node,
    execute_actions_node,
    hitl_node,
    intent_classifier_node,
    inventory_agent_node,
    join_findings_node,
    marketing_agent_node,
    memory_retrieve_node,
    persist_incident_node,
    reflection_node,
    sales_agent_node,
    support_agent_node,
    synthesizer_node,
)
from app.graph.state import AgentState


def build_graph() -> StateGraph:
    """Construct the graph topology (stubs; routing edges correct).

    Returns:
        StateGraph with all nodes wired and conditional edges defined.
    """
    g = StateGraph(AgentState)

    # ── Add all nodes ──
    g.add_node("intent_classifier", intent_classifier_node)
    g.add_node("sales_agent", sales_agent_node)
    g.add_node("inventory_agent", inventory_agent_node)
    g.add_node("marketing_agent", marketing_agent_node)
    g.add_node("support_agent", support_agent_node)
    g.add_node("memory_retrieve", memory_retrieve_node)
    g.add_node("join_findings", join_findings_node)
    g.add_node("synthesizer", synthesizer_node)
    g.add_node("reflection", reflection_node)
    g.add_node("action_agent", action_agent_node)
    g.add_node("hitl_node", hitl_node)
    g.add_node("execute_actions", execute_actions_node)
    g.add_node("assemble_response", assemble_response_node)
    g.add_node("persist_incident", persist_incident_node)

    # ── Add edges ──

    # START → intent_classifier
    g.add_edge(START, "intent_classifier")

    # intent_classifier → (conditional) route_after_intent
    g.add_conditional_edges("intent_classifier", route_after_intent)

    # Domain agents + memory_retrieve → join_findings (parallel fan-out)
    for node in [
        "sales_agent",
        "inventory_agent",
        "marketing_agent",
        "support_agent",
        "memory_retrieve",
    ]:
        g.add_edge(node, "join_findings")

    # join_findings → synthesizer
    g.add_edge("join_findings", "synthesizer")

    # synthesizer → reflection
    g.add_edge("synthesizer", "reflection")

    # reflection → (conditional) route_after_reflection (retry or pass)
    g.add_conditional_edges("reflection", route_after_reflection)

    # action_agent → (conditional) route_after_action_agent (hitl or assemble)
    g.add_conditional_edges("action_agent", route_after_action_agent)

    # hitl_node → (conditional) route_after_hitl (execute or assemble)
    g.add_conditional_edges("hitl_node", route_after_hitl)

    # execute_actions → assemble_response
    g.add_edge("execute_actions", "assemble_response")

    # assemble_response → persist_incident
    g.add_edge("assemble_response", "persist_incident")

    # persist_incident → END
    g.add_edge("persist_incident", END)

    return g


async def compile_graph(checkpointer: AsyncPostgresSaver):
    """Compile the graph with checkpointer and recursion limit.

    Args:
        checkpointer: AsyncPostgresSaver instance (initialized by runtime.py)

    Returns:
        Compiled, runnable graph with recursion_limit enforced.
    """
    graph = build_graph()
    compiled = graph.compile(
        checkpointer=checkpointer,
    )
    return compiled.with_config({"recursion_limit": settings.RECURSION_LIMIT})
