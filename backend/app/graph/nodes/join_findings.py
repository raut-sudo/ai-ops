"""Join findings node stub.

Synchronization barrier after parallel fan-out. Pass-through.
"""

from __future__ import annotations


async def join_findings_node(state: dict) -> dict:
    """Synchronization barrier between fan-out workers and synthesis.

    The reducer has already merged `domain_findings`; this node remains a no-op
    by design to make fan-out completion explicit in topology and traces.
    """
    return {}
