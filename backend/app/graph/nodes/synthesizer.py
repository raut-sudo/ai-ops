"""Synthesizer node stub.

LLM structured output: produces SynthesisResult from domain findings + memory.
"""

from __future__ import annotations

from app.schemas import SynthesisResult


async def synthesizer_node(state: dict) -> dict:
    """Stub: synthesize findings into diagnosis."""
    return {
        "synthesis": SynthesisResult(
            correlated_explanation="Stub: no correlated explanation yet.",
            root_causes=[],
            contributing_factors={},
            confidence_score=0.5,
            recommendations=[],
            domains_correlated=[],
        )
    }
