"""LangGraph construction: build_graph() + compile_graph().

9-node topology:
  START → orchestrator
  orchestrator → [domain agents] + optional memory_agent (parallel fan-out)
  domain agents / memory_agent → synthesizer
  synthesizer → reflection
  reflection → (retry fan-out OR response_composer)
  response_composer → END"""

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.graph.edges import (
    route_after_orchestrator,
    route_after_reflection,
)
from app.graph.nodes import (
    inventory_agent_node,
    marketing_agent_node,
    memory_agent_node,
    orchestrator_node,
    reflection_node,
    response_composer_node,
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
    g.add_node("orchestrator", orchestrator_node)

    # Parallel domain agents
    g.add_node("sales_agent", sales_agent_node)
    g.add_node("inventory_agent", inventory_agent_node)
    g.add_node("marketing_agent", marketing_agent_node)
    g.add_node("support_agent", support_agent_node)
    g.add_node("memory_agent", memory_agent_node)

    # Core pipeline
    g.add_node("synthesizer", synthesizer_node)
    g.add_node("reflection", reflection_node)
    g.add_node("response_composer", response_composer_node)

    # ── Add edges ──

    # START → orchestrator
    g.add_edge(START, "orchestrator")

    # orchestrator → conditional (fan-out to domain agents, memory_agent, or response_composer)
    g.add_conditional_edges("orchestrator", route_after_orchestrator)

    # Domain agents + memory_agent → synthesizer (parallel fan-out convergence)
    for node in [
        "sales_agent",
        "inventory_agent",
        "marketing_agent",
        "support_agent",
        "memory_agent",
    ]:
        g.add_edge(node, "synthesizer")

    # synthesizer → reflection
    g.add_edge("synthesizer", "reflection")

    # reflection → conditional (retry fan-out OR response_composer)
    g.add_conditional_edges("reflection", route_after_reflection)

    # response_composer → END
    g.add_edge("response_composer", END)

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
