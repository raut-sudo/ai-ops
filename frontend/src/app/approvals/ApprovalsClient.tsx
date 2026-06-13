"use client";

import { useEffect, useState, useCallback } from "react";
import { CheckCircle2, XCircle, RefreshCw, AlertTriangle } from "lucide-react";
import { fetchPendingActions, postApprove } from "@/lib/api";
import { formatRelativeTime, cn } from "@/lib/utils";
import { getUserId } from "@/lib/auth";
import type { PendingAction } from "@/lib/types";

// ── Toast ──────────────────────────────────────────────────────────────────────

function Toast({ message, variant }: { message: string; variant: "success" | "error" }) {
  return (
    <div
      className={cn(
        "fixed bottom-6 right-6 z-50 px-4 py-3 rounded-lg shadow-lg text-sm font-medium",
        variant === "success"
          ? "bg-success text-white"
          : "bg-danger text-white"
      )}
    >
      {message}
    </div>
  );
}

// ── Risk badge ──────────────────────────────────────────────────────────────────

const riskBadge: Record<string, string> = {
  high: "bg-danger/10 text-danger",
  medium: "bg-warning/10 text-warning",
  low: "bg-success/10 text-success",
};

// ── Per-group section ──────────────────────────────────────────────────────────

interface GroupProps {
  sessionId: string;
  actions: PendingAction[];
  onApproveAll: (sessionId: string, actionIds: string[]) => Promise<void>;
  onRejectAll: (sessionId: string, actionIds: string[]) => Promise<void>;
  submitting: boolean;
}

function SessionGroup({ sessionId, actions, onApproveAll, onRejectAll, submitting }: GroupProps) {
  const ids = actions.map((a) => a.action_id);
  return (
    <div className="border border-border rounded-xl p-4 space-y-3">
      {/* Group header */}
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-mono text-text-muted truncate max-w-xs">
          session: {sessionId}
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={() => onRejectAll(sessionId, ids)}
            disabled={submitting}
            className={cn(
              "flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
              submitting
                ? "bg-surface-3 text-text-muted cursor-not-allowed"
                : "bg-danger/10 hover:bg-danger/20 text-danger cursor-pointer"
            )}
          >
            <XCircle size={13} />
            Reject All
          </button>
          <button
            onClick={() => onApproveAll(sessionId, ids)}
            disabled={submitting}
            className={cn(
              "flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
              submitting
                ? "bg-surface-3 text-text-muted cursor-not-allowed"
                : "bg-success/10 hover:bg-success/20 text-success cursor-pointer"
            )}
          >
            <CheckCircle2 size={13} />
            Approve All
          </button>
        </div>
      </div>

      {/* Action cards */}
      <div className="space-y-2">
        {actions.map((action) => (
          <div key={action.action_id} className="bg-surface-2 rounded-lg p-3 space-y-1">
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm font-mono text-text-primary">{action.action_type}</p>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span
                  className={`text-xs px-2 py-0.5 rounded font-medium ${
                    riskBadge[action.risk_level] ?? "bg-surface-3 text-text-secondary"
                  }`}
                >
                  {action.risk_level}
                </span>
                <span className="text-xs text-text-muted">
                  {formatRelativeTime(action.created_at)}
                </span>
              </div>
            </div>
            <p className="text-xs text-text-muted">{action.target}</p>
            {action.justification && (
              <p className="text-xs text-text-secondary">{action.justification}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main client component ──────────────────────────────────────────────────────

export default function ApprovalsClient() {
  const [groups, setGroups] = useState<Record<string, PendingAction[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<{ message: string; variant: "success" | "error" } | null>(null);

  function showToast(message: string, variant: "success" | "error") {
    setToast({ message, variant });
    setTimeout(() => setToast(null), 3000);
  }

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const actions = await fetchPendingActions();
      const grouped: Record<string, PendingAction[]> = {};
      for (const action of actions) {
        if (!grouped[action.session_id]) grouped[action.session_id] = [];
        grouped[action.session_id].push(action);
      }
      setGroups(grouped);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load pending actions");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function handleApproveAll(sessionId: string, actionIds: string[]) {
    setSubmitting(true);
    try {
      await postApprove(sessionId, {
        approved_action_ids: actionIds,
        rejected_action_ids: [],
        approver: getUserId(),
      });
      setGroups((prev) => {
        const next = { ...prev };
        delete next[sessionId];
        return next;
      });
      showToast("Actions executed", "success");
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Approve failed", "error");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRejectAll(sessionId: string, actionIds: string[]) {
    setSubmitting(true);
    try {
      await postApprove(sessionId, {
        approved_action_ids: [],
        rejected_action_ids: actionIds,
        approver: getUserId(),
      });
      setGroups((prev) => {
        const next = { ...prev };
        delete next[sessionId];
        return next;
      });
      showToast("Actions rejected", "success");
    } catch (e) {
      showToast(e instanceof Error ? e.message : "Reject failed", "error");
    } finally {
      setSubmitting(false);
    }
  }

  const sessionIds = Object.keys(groups);
  const totalActions = sessionIds.reduce((n, k) => n + groups[k].length, 0);

  return (
    <div className="p-6 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-text-primary">
          Approvals{" "}
          {totalActions > 0 && (
            <span className="ml-1 text-xs text-text-muted">
              ({totalActions} pending in {sessionIds.length} session
              {sessionIds.length !== 1 ? "s" : ""})
            </span>
          )}
        </h1>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary transition-colors"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-sm text-danger">
          <AlertTriangle size={14} />
          {error}
        </div>
      )}

      {!loading && !error && sessionIds.length === 0 && (
        <p className="text-sm text-text-muted">No actions pending approval.</p>
      )}

      {/* Groups */}
      <div className="space-y-4">
        {sessionIds.map((sessionId) => (
          <SessionGroup
            key={sessionId}
            sessionId={sessionId}
            actions={groups[sessionId]}
            onApproveAll={handleApproveAll}
            onRejectAll={handleRejectAll}
            submitting={submitting}
          />
        ))}
      </div>

      {/* Toast */}
      {toast && <Toast message={toast.message} variant={toast.variant} />}
    </div>
  );
}
