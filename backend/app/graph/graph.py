"""LangGraph construction: build_graph() + compile_graph().

6-node topology:
  START → intent_classifier
  intent_classifier → [domain agents] + optional memory_retrieve (parallel fan-out)
  domain agents / memory_retrieve → synthesizer
  synthesizer → reflection
  reflection → (retry fan-out OR aggregator)
  aggregator → END
"""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.graph.edges import (
    route_after_intent,
    route_after_reflection,
)
from app.graph.nodes import (
    aggregator_node,
    intent_classifier_node,
    inventory_agent_node,
    marketing_agent_node,
    memory_retrieve_node,
    reflection_node,
    sales_agent_node,
    support_agent_node,
    synthesizer_node,
)
from app.graph.state import AgentState


def build_graph() -> StateGraph:
    """Construct the 6-node graph topology.

    Returns:
        StateGraph with all nodes wired and conditional edges defined.
    """
    g = StateGraph(AgentState)

    # ── Add nodes ──
    g.add_node("intent_classifier", intent_classifier_node)

    # Parallel domain agents
    g.add_node("sales_agent", sales_agent_node)
    g.add_node("inventory_agent", inventory_agent_node)
    g.add_node("marketing_agent", marketing_agent_node)
    g.add_node("support_agent", support_agent_node)
    g.add_node("memory_retrieve", memory_retrieve_node)

    # Core pipeline
    g.add_node("synthesizer", synthesizer_node)
    g.add_node("reflection", reflection_node)
    g.add_node("aggregator", aggregator_node)

    # ── Add edges ──

    # START → intent_classifier
    g.add_edge(START, "intent_classifier")

    # intent_classifier → conditional (fan-out to domain agents, memory, or aggregator)
    g.add_conditional_edges("intent_classifier", route_after_intent)

    # Domain agents + memory_retrieve → synthesizer (parallel fan-out convergence)
    for node in [
        "sales_agent",
        "inventory_agent",
        "marketing_agent",
        "support_agent",
        "memory_retrieve",
    ]:
        g.add_edge(node, "synthesizer")

    # synthesizer → reflection
    g.add_edge("synthesizer", "reflection")

    # reflection → conditional (retry fan-out OR aggregator)
    g.add_conditional_edges("reflection", route_after_reflection)

    # aggregator → END
    g.add_edge("aggregator", END)

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
