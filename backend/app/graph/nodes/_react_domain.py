from __future__ import annotations

import asyncio
import importlib
import json
import re
from typing import Any

from app.config import settings
from app.schemas import DomainFinding, MetricSnapshot
from app.tools.adapters import READ_TOOLS
from app.tools.inventory import get_low_stock_products, get_stock_level
from app.tools.marketing import get_campaign_performance
from app.tools.sales import get_sales_metrics
from app.tools.support import get_support_metrics

_DOMAIN_TOOL_NAMES: dict[str, set[str]] = {
    "sales": {
        "get_sales_metrics",
        "compare_sales_periods",
        "get_top_products",
        "get_sales_by_region",
    },
    "inventory": {
        "get_stock_level",
        "get_low_stock_products",
        "was_out_of_stock",
        "get_stockout_history",
    },
    "marketing": {
        "get_campaign_performance",
        "get_underperforming_campaigns",
        "get_active_campaigns_for_sku",
    },
    "support": {
        "get_support_metrics",
        "get_complaints_by_sku",
        "get_refund_rate",
        "get_ticket_trends",
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
        metrics = await get_sales_metrics(period="yesterday")
        findings: list[str] = []
        anomalies: list[str] = []
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
                    tool_calls_made=["get_sales_metrics"],
                    severity=severity,
                )
            }
        }

    if domain == "inventory":
        sku_matches = re.findall(r"SKU[-_ ]?\d+", query.upper())
        sku = sku_matches[0].replace("_", "-").replace(" ", "-") if sku_matches else "SKU-101"
        stock = await get_stock_level(sku)
        low = await get_low_stock_products()
        qty = int(stock.get("quantity_on_hand", 0)) if stock else 0
        reorder = int(stock.get("reorder_point", 0)) if stock else 0
        findings = [f"{sku} quantity_on_hand={qty}, reorder_point={reorder}."]
        anomalies: list[str] = []
        severity = "low"
        if stock and qty <= 0:
            anomalies.append(f"{sku} is out of stock.")
            severity = "critical"
        elif stock and qty <= reorder:
            anomalies.append(f"{sku} is below reorder threshold.")
            severity = "high"
        if low:
            findings.append(f"{len(low)} SKU(s) flagged as low stock.")
        metrics = [
            MetricSnapshot(name="quantity_on_hand", value=qty, unit="units", period="now"),
            MetricSnapshot(name="reorder_point", value=reorder, unit="units", period="now"),
            MetricSnapshot(name="low_stock_sku_count", value=len(low), unit="count", period="now"),
        ]
        return {
            "domain_findings": {
                domain: DomainFinding(
                    domain=domain,  # type: ignore[arg-type]
                    findings=findings,
                    metrics=metrics,
                    anomalies=anomalies,
                    confidence=0.86 if anomalies else 0.68,
                    tool_calls_made=["get_stock_level", "get_low_stock_products"],
                    severity=severity,
                )
            }
        }

    if domain == "marketing":
        campaigns = await get_campaign_performance(period="yesterday")
        paused = [c for c in campaigns if c.get("status") == "paused"]
        low_roas = [c for c in campaigns if float(c.get("roas", 0.0)) < 1.0]
        findings: list[str] = []
        anomalies: list[str] = []
        if paused:
            findings.append(f"{len(paused)} paused campaign(s) detected.")
            anomalies.append("Paused campaign state may suppress demand.")
        if low_roas:
            findings.append(f"{len(low_roas)} campaign(s) have ROAS below 1.0.")
        if not findings:
            findings.append("No material campaign anomalies detected in fallback analysis.")
        metrics = [
            MetricSnapshot(
                name="campaign_count", value=len(campaigns), unit="count", period="yesterday"
            ),
            MetricSnapshot(
                name="paused_campaign_count", value=len(paused), unit="count", period="yesterday"
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
                    tool_calls_made=["get_campaign_performance"],
                    severity="high" if anomalies else "low",
                )
            }
        }

    if domain == "support":
        metrics = await get_support_metrics(period="yesterday")
        negatives = 0
        for m in metrics:
            if m.name == "negative_ticket_count":
                negatives = int(m.value)
                break
        findings = [f"Negative ticket count yesterday: {negatives}."]
        anomalies = ["Customer sentiment deterioration detected."] if negatives >= 5 else []
        return {
            "domain_findings": {
                domain: DomainFinding(
                    domain=domain,  # type: ignore[arg-type]
                    findings=findings,
                    metrics=metrics,
                    anomalies=anomalies,
                    confidence=0.75 if anomalies else 0.6,
                    tool_calls_made=["get_support_metrics"],
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
            temperature=0,
        )
        agent = create_react_agent(model=llm, tools=_domain_tools(domain))
        result = await asyncio.wait_for(
            agent.ainvoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": _prompt(domain, state.get("query", "")),
                        }
                    ]
                }
            ),
            timeout=8.0,
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
        parsed = _parse_domain_response(domain, last_text)
        finding = parsed["domain_findings"][domain]
        if finding.tool_calls_made:
            return parsed
        return await _fallback(state, domain)
    except Exception:
        return await _fallback(state, domain)
