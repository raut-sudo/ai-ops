/**
 * Tiny auth stub — reads userId from localStorage or falls back to "demo-user".
 * In production this would be replaced by a real session/JWT lookup.
 */
export function getUserId(): string {
  if (typeof window === "undefined") return "demo-user";
  return localStorage.getItem("ai_ops_user_id") ?? "demo-user";
}

export function setUserId(id: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem("ai_ops_user_id", id);
}

// ── Recent sessions (used by /observability) ─────────────────────────────────

interface SessionEntry {
  thread_id: string;
  otel_trace_id: string | null;
  query: string;
  ts: string;
}

const SESSION_KEY = "ai_ops_recent_sessions";
const MAX_SESSIONS = 10;

export function recordSession(entry: SessionEntry): void {
  if (typeof window === "undefined") return;
  const existing: SessionEntry[] = JSON.parse(
    localStorage.getItem(SESSION_KEY) ?? "[]"
  );
  const updated = [entry, ...existing].slice(0, MAX_SESSIONS);
  localStorage.setItem(SESSION_KEY, JSON.stringify(updated));
}

export function getRecentSessions(): SessionEntry[] {
  if (typeof window === "undefined") return [];
  return JSON.parse(localStorage.getItem(SESSION_KEY) ?? "[]");
}
