from __future__ import annotations

import asyncio
import importlib
import json
import re
from typing import Any

import structlog
from langgraph.errors import GraphRecursionError

from app.config import settings
from app.schemas import DomainFinding, MetricSnapshot
from app.tools.adapters import READ_TOOLS
from app.tools.inventory import analyze_inventory, get_stock_level
from app.tools.marketing import analyze_marketing
from app.tools.sales import analyze_sales
from app.tools.support import analyze_support

logger = structlog.get_logger(__name__)

_DOMAIN_TOOL_NAMES: dict[str, set[str]] = {
    "sales": {
        "analyze_sales",
        "get_top_products",
        "get_declining_products",
        "get_sales_distribution",
    },
    "inventory": {
        "analyze_inventory",
        "get_stock_level",
        "get_stockout_history",
        "get_inventory_turnover",
        "get_revenue_lost_to_stockouts",
    },
    "marketing": {
        "analyze_marketing",
        "get_underperforming_campaigns",
        "get_campaigns_for_sku",
        "get_unpromoted_top_products",
    },
    "support": {
        "analyze_support",
        "get_products_with_high_complaint_rate",
        "get_common_complaint_categories",
        "get_common_return_reasons",
        "get_churn_risk_products",
    },
}


def _domain_tools(domain: str) -> list[Any]:
    names = _DOMAIN_TOOL_NAMES[domain]
    return [t for t in READ_TOOLS if t.name in names]


def _stub_finding(domain: str) -> dict:
    """Return a minimal DomainFinding when DB/tools are unavailable."""
    return {
        "domain_findings": {
            domain: DomainFinding(
                domain=domain,  # type: ignore[arg-type]
                findings=[f"{domain}: infrastructure unavailable; stub finding returned."],
                metrics=[MetricSnapshot(name="stub", value=0, unit="flag", period="runtime")],
                anomalies=[],
                confidence=0.3,
                tool_calls_made=[],
                severity="low",
            )
        }
    }


async def _fallback(state: dict, domain: str) -> dict:
    query = state.get("query", "")

    try:
        return await _fallback_inner(state, domain, query)
    except Exception:
        return _stub_finding(domain)


async def _fallback_inner(state: dict, domain: str, query: str) -> dict:
    if domain == "sales":
        result = await analyze_sales(period="yesterday", compare_previous=True)
        findings: list[str] = []
        anomalies: list[str] = []
        metrics: list[MetricSnapshot] = [
            MetricSnapshot(
                name="revenue",
                value=result["revenue"],
                unit="USD",
                period="yesterday",
                delta_pct=result.get("revenue_delta_pct"),
            ),
            MetricSnapshot(
                name="order_count",
                value=result["order_count"],
                unit="orders",
                period="yesterday",
                delta_pct=result.get("order_count_delta_pct"),
            ),
            MetricSnapshot(
                name="average_order_value",
                value=result["average_order_value"],
                unit="USD",
                period="yesterday",
                delta_pct=result.get("aov_delta_pct"),
            ),
            MetricSnapshot(
                name="units_sold",
                value=result["units_sold"],
                unit="units",
                period="yesterday",
                delta_pct=result.get("units_sold_delta_pct"),
            ),
        ]
        for m in metrics:
            if (
                m.name in {"revenue", "order_count"}
                and (m.delta_pct is not None)
                and m.delta_pct < 0
            ):
                findings.append(f"{m.name} declined {abs(m.delta_pct):.1f}% yesterday.")
                if m.delta_pct <= -20:
                    anomalies.append(f"{m.name} materially below baseline.")
        if not findings:
            findings.append("No major sales decline detected in fallback analysis.")
        severity = "high" if anomalies else "low"
        confidence = 0.8 if anomalies else 0.6
        return {
            "domain_findings": {
                domain: DomainFinding(
                    domain=domain,  # type: ignore[arg-type]
                    findings=findings,
                    metrics=metrics,
                    anomalies=anomalies,
                    confidence=confidence,
                    tool_calls_made=["analyze_sales"],
                    severity=severity,
                )
            }
        }

    if domain == "inventory":
        sku_matches = re.findall(r"SKU[-_ ]?\d+", query.upper())
        sku = sku_matches[0].replace("_", "-").replace(" ", "-") if sku_matches else "SKU-101"
        stock = await get_stock_level(sku)
        inv_summary = await analyze_inventory()
        qty = int(stock.get("quantity_on_hand", 0)) if stock else 0
        reorder = int(stock.get("reorder_point", 0)) if stock else 0
        low_count = int(inv_summary.get("low_stock_count", 0))
        findings = [f"{sku} quantity_on_hand={qty}, reorder_point={reorder}."]
        anomalies: list[str] = []
        severity = "low"
        if stock and qty <= 0:
            anomalies.append(f"{sku} is out of stock.")
            severity = "critical"
        elif stock and qty <= reorder:
            anomalies.append(f"{sku} is below reorder threshold.")
            severity = "high"
        if low_count:
            findings.append(f"{low_count} SKU(s) flagged as low stock.")
        metrics = [
            MetricSnapshot(name="quantity_on_hand", value=qty, unit="units", period="now"),
            MetricSnapshot(name="reorder_point", value=reorder, unit="units", period="now"),
            MetricSnapshot(name="low_stock_sku_count", value=low_count, unit="count", period="now"),
        ]
        return {
            "domain_findings": {
                domain: DomainFinding(
                    domain=domain,  # type: ignore[arg-type]
                    findings=findings,
                    metrics=metrics,
                    anomalies=anomalies,
                    confidence=0.86 if anomalies else 0.68,
                    tool_calls_made=["get_stock_level", "analyze_inventory"],
                    severity=severity,
                )
            }
        }

    if domain == "marketing":
        summary = await analyze_marketing(period="yesterday")
        paused_count = (
            sum(1 for c in summary.get("campaigns", []) if c.get("status") == "paused")
            if "campaigns" in summary
            else 0
        )
        low_roas = summary.get("overall_roas", 1.0) < 1.0
        findings: list[str] = []
        anomalies: list[str] = []
        if paused_count:
            findings.append(f"{paused_count} paused campaign(s) detected.")
            anomalies.append("Paused campaign state may suppress demand.")
        if low_roas:
            findings.append(f"Overall ROAS is {summary.get('overall_roas', 0):.2f} (below 1.0).")
            anomalies.append("Marketing spend is not recovering its cost.")
        if not findings:
            findings.append("No material campaign anomalies detected in fallback analysis.")
        metrics = [
            MetricSnapshot(
                name="campaign_count",
                value=summary.get("campaign_count", 0),
                unit="count",
                period="yesterday",
            ),
            MetricSnapshot(
                name="overall_roas",
                value=summary.get("overall_roas", 0.0),
                unit="ratio",
                period="yesterday",
            ),
            MetricSnapshot(
                name="total_spend",
                value=summary.get("total_spend", 0.0),
                unit="USD",
                period="yesterday",
            ),
        ]
        return {
            "domain_findings": {
                domain: DomainFinding(
                    domain=domain,  # type: ignore[arg-type]
                    findings=findings,
                    metrics=metrics,
                    anomalies=anomalies,
                    confidence=0.82 if anomalies else 0.62,
                    tool_calls_made=["analyze_marketing"],
                    severity="high" if anomalies else "low",
                )
            }
        }

    if domain == "support":
        summary = await analyze_support(period="yesterday")
        negatives = int(summary.get("negative_ticket_count", 0))
        refund_rate = float(summary.get("refund_rate_pct", 0.0))
        findings = [
            f"Negative ticket count yesterday: {negatives}.",
            f"Refund rate: {refund_rate:.1f}%.",
        ]
        anomalies = []
        if negatives >= 5:
            anomalies.append("Customer sentiment deterioration detected.")
        if refund_rate >= 10.0:
            anomalies.append("Refund rate is elevated (>=10%).")
        metrics = [
            MetricSnapshot(
                name="ticket_count",
                value=summary.get("ticket_count", 0),
                unit="tickets",
                period="yesterday",
            ),
            MetricSnapshot(
                name="average_sentiment",
                value=summary.get("average_sentiment", 0.0),
                unit="score",
                period="yesterday",
            ),
            MetricSnapshot(
                name="negative_ticket_count",
                value=negatives,
                unit="tickets",
                period="yesterday",
            ),
            MetricSnapshot(
                name="refund_rate_pct",
                value=refund_rate,
                unit="percent",
                period="yesterday",
            ),
        ]
        return {
            "domain_findings": {
                domain: DomainFinding(
                    domain=domain,  # type: ignore[arg-type]
                    findings=findings,
                    metrics=metrics,
                    anomalies=anomalies,
                    confidence=0.75 if anomalies else 0.6,
                    tool_calls_made=["analyze_support"],
                    severity="high" if anomalies else "low",
                )
            }
        }

    return {
        "domain_findings": {
            domain: DomainFinding(
                domain=domain,  # type: ignore[arg-type]
                findings=["Fallback path used because ReAct model is unavailable."],
                metrics=[
                    MetricSnapshot(
                        name="fallback",
                        value=1,
                        unit="flag",
                        period="runtime",
                    )
                ],
                anomalies=[],
                confidence=0.5,
                tool_calls_made=[],
                severity="low",
            )
        }
    }


def _prompt(domain: str, query: str) -> str:
    return (
        "You are a domain specialist agent in an e-commerce diagnostics graph. "
        f"Your domain is: {domain}. Use the available tools to investigate the user's query. "
        "Return ONLY JSON with keys: findings (list[str]), anomalies (list[str]), "
        "confidence (0..1), severity (low|medium|high|critical), metrics (list of objects with "
        "name/value/unit/period[/delta_pct]), and tool_calls_made (list[str]). "
        f"User query: {query}"
    )


def _as_metric(item: dict[str, Any]) -> MetricSnapshot | None:
    try:
        return MetricSnapshot(
            name=str(item["name"]),
            value=item["value"],
            unit=str(item["unit"]),
            period=str(item["period"]),
            delta_pct=item.get("delta_pct"),
        )
    except Exception:
        return None


def _parse_domain_response(domain: str, raw: str) -> dict:
    try:
        data = json.loads(raw)
    except Exception:
        return {
            "domain_findings": {
                domain: DomainFinding(
                    domain=domain,  # type: ignore[arg-type]
                    findings=["Agent response parsing failed; fallback required."],
                    metrics=[
                        MetricSnapshot(name="parse_error", value=1, unit="flag", period="runtime")
                    ],
                    anomalies=[],
                    confidence=0.4,
                    tool_calls_made=[],
                    severity="low",
                )
            }
        }

    metrics_in = data.get("metrics", [])
    metrics: list[MetricSnapshot] = []
    if isinstance(metrics_in, list):
        for m in metrics_in:
            if isinstance(m, dict):
                metric = _as_metric(m)
                if metric is not None:
                    metrics.append(metric)

    severity = data.get("severity", "low")
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "low"

    confidence_raw = data.get("confidence", 0.5)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    finding = DomainFinding(
        domain=domain,  # type: ignore[arg-type]
        findings=[str(x) for x in data.get("findings", []) if isinstance(x, str)],
        metrics=metrics,
        anomalies=[str(x) for x in data.get("anomalies", []) if isinstance(x, str)],
        confidence=confidence,
        tool_calls_made=[str(x) for x in data.get("tool_calls_made", []) if isinstance(x, str)],
        severity=severity,
    )
    return {"domain_findings": {domain: finding}}


async def run_domain_react_agent(state: dict, domain: str) -> dict:
    import os

    if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
        return await _fallback(state, domain)

    try:
        AzureChatOpenAI = importlib.import_module("langchain_openai").AzureChatOpenAI
        create_react_agent = importlib.import_module("langgraph.prebuilt").create_react_agent

        llm = AzureChatOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_GPT4O,
            temperature=float(os.getenv("AZURE_TEMPRATURE", 0.8)),
        )
        agent = create_react_agent(model=llm, tools=_domain_tools(domain))

        # Prepend conversation history so the ReAct agent is aware of prior
        # turns (e.g. "same thing for inventory" resolves against the history).
        prior_messages = state.get("messages", [])
        domain_prompt = {"role": "user", "content": _prompt(domain, state.get("query", ""))}

        result = await asyncio.wait_for(
            agent.ainvoke(
                {
                    "messages": [
                        *prior_messages,
                        domain_prompt,
                    ]
                },
                config={"recursion_limit": 21},
            ),
            timeout=60.0,
        )
        messages = result.get("messages", [])
        last_text = ""
        for msg in reversed(messages):
            content = getattr(msg, "content", "")
            if isinstance(content, str) and content.strip():
                last_text = content
                break
            if isinstance(content, list):
                joined = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        joined.append(str(part.get("text", "")))
                if joined:
                    last_text = "\n".join(joined)
                    break

        if not last_text.strip():
            return await _fallback(state, domain)
        return _parse_domain_response(domain, last_text)
    except (TimeoutError, GraphRecursionError):
        logger.warning("domain_agent_terminated", domain=domain, exc_info=True)
        return await _fallback(state, domain)
    except Exception as exc:
        logger.warning("domain_agent_error", domain=domain, error=str(exc), exc_info=True)
        return await _fallback(state, domain)
