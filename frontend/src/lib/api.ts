/**
 * Fetch wrappers for the backend API.
 * All paths are relative (/api/v1/...) — Next.js rewrites handle the proxy.
 * X-User-Id is injected on every call (§19.2 AuthMiddleware).
 */

import type {
  IncidentSummary,
  IncidentDetail,
  PendingAction,
  StockLevel,
  HITLDecision,
  FinalResponse,
} from "@/lib/types";
import { getUserId } from "@/lib/auth";

// In a real production deployment this would come from a proper auth session.
// For the demo we read from localStorage (set via Sidebar user pill).
function authHeaders(): HeadersInit {
  return {
    "Content-Type": "application/json",
    "X-User-Id": getUserId(),
  };
}

// ── Chat ─────────────────────────────────────────────────────────────────────

export async function postChat(query: string, threadId?: string): Promise<Response> {
  return fetch("/api/v1/chat", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ query, thread_id: threadId ?? undefined }),
  });
}

// ── Approve ───────────────────────────────────────────────────────────────────

export async function postApprove(
  threadId: string,
  decision: HITLDecision
): Promise<FinalResponse> {
  const res = await fetch("/api/v1/approve", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({ thread_id: threadId, decision }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error((err as { detail?: string }).detail ?? "approve failed");
  }
  return res.json() as Promise<FinalResponse>;
}

// ── Incidents ─────────────────────────────────────────────────────────────────

export async function fetchIncidents(): Promise<IncidentSummary[]> {
  const res = await fetch("/api/v1/incidents", { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to load incidents");
  const data = (await res.json()) as { incidents: IncidentSummary[] };
  return data.incidents;
}

export async function fetchIncident(id: string): Promise<IncidentDetail> {
  const res = await fetch(`/api/v1/incidents/${id}`, { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to load incident");
  return res.json() as Promise<IncidentDetail>;
}

// ── Actions ───────────────────────────────────────────────────────────────────

export async function fetchPendingActions(): Promise<PendingAction[]> {
  const res = await fetch("/api/v1/actions/pending", { headers: authHeaders() });
  if (!res.ok) throw new Error("Failed to load actions");
  const data = (await res.json()) as { actions: PendingAction[] };
  return data.actions;
}

// ── Operational (stock) ───────────────────────────────────────────────────────

export async function fetchStock(sku: string): Promise<StockLevel> {
  const res = await fetch(`/api/v1/operational/stock/${encodeURIComponent(sku)}`, {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`Failed to load stock for ${sku}`);
  return res.json() as Promise<StockLevel>;
}
