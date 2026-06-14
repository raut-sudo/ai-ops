from __future__ import annotations

from app.graph.graph import build_graph


def test_graph_nodes_match_blueprint_topology() -> None:
    graph = build_graph()

    expected_nodes = {
        "intent_classifier",
        "sales_agent",
        "inventory_agent",
        "marketing_agent",
        "support_agent",
        "memory_retrieve",
        "synthesizer",
        "reflection",
        "aggregator",
    }

    assert set(graph.nodes.keys()) == expected_nodes


def test_graph_unconditional_edges_match_blueprint() -> None:
    graph = build_graph()

    expected_edges = {
        ("__start__", "intent_classifier"),
        ("sales_agent", "synthesizer"),
        ("inventory_agent", "synthesizer"),
        ("marketing_agent", "synthesizer"),
        ("support_agent", "synthesizer"),
        ("memory_retrieve", "synthesizer"),
        ("synthesizer", "reflection"),
        ("aggregator", "__end__"),
    }

    assert graph.edges == expected_edges


def test_graph_conditional_branches_match_blueprint() -> None:
    graph = build_graph()

    expected_branch_nodes = {
        "intent_classifier": "route_after_intent",
        "reflection": "route_after_reflection",
    }

    assert set(graph.branches.keys()) == set(expected_branch_nodes.keys())

    for node_name, route_name in expected_branch_nodes.items():
        assert route_name in graph.branches[node_name]
