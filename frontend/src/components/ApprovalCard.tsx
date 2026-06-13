"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ActionProposal } from "@/lib/types";

interface ApprovalCardProps {
  proposals: ActionProposal[];
  onSubmit: (approvedIds: string[], rejectedIds: string[]) => void;
  disabled?: boolean;
}

const riskColor: Record<string, string> = {
  high: "text-danger border-danger/30 bg-danger/5",
  medium: "text-warning border-warning/30 bg-warning/5",
  low: "text-success border-success/30 bg-success/5",
};

export default function ApprovalCard({ proposals, onSubmit, disabled = false }: ApprovalCardProps) {
  const [approved, setApproved] = useState<Set<string>>(
    () => new Set(proposals.map((p) => p.action_id))
  );

  function toggle(id: string) {
    setApproved((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function handleSubmit() {
    const approvedIds = proposals
      .filter((p) => approved.has(p.action_id))
      .map((p) => p.action_id);
    const rejectedIds = proposals
      .filter((p) => !approved.has(p.action_id))
      .map((p) => p.action_id);
    onSubmit(approvedIds, rejectedIds);
  }

  return (
    <div className="border border-warning/40 rounded-xl p-4 space-y-4 w-full max-w-2xl mx-auto bg-warning/5">
      {/* Header */}
      <div className="flex items-center gap-2">
        <AlertTriangle size={16} className="text-warning flex-shrink-0" />
        <span className="text-sm font-semibold text-text-primary">
          {proposals.length} action{proposals.length !== 1 ? "s" : ""} pending your approval
        </span>
      </div>

      {/* Action list */}
      <div className="space-y-2">
        {proposals.map((proposal) => {
          const isApproved = approved.has(proposal.action_id);
          return (
            <label
              key={proposal.action_id}
              className={cn(
                "flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors select-none",
                riskColor[proposal.risk_level] ?? "text-text-secondary border-border",
                !isApproved && "opacity-60"
              )}
            >
              <input
                type="checkbox"
                checked={isApproved}
                onChange={() => toggle(proposal.action_id)}
                disabled={disabled}
                className="mt-0.5 accent-accent"
              />
              <div className="flex-1 min-w-0 space-y-0.5">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-text-primary font-mono">
                    {proposal.action_type}
                  </span>
                  <span className="text-xs uppercase tracking-wide">
                    [{proposal.risk_level}]
                  </span>
                </div>
                <p className="text-xs text-text-secondary truncate">
                  {proposal.target}
                </p>
                <p className="text-xs text-text-muted">{proposal.justification}</p>
              </div>
              {isApproved ? (
                <CheckCircle2 size={16} className="text-success flex-shrink-0 mt-0.5" />
              ) : (
                <XCircle size={16} className="text-text-muted flex-shrink-0 mt-0.5" />
              )}
            </label>
          );
        })}
      </div>

      {/* Submit */}
      <button
        onClick={handleSubmit}
        disabled={disabled}
        className={cn(
          "w-full py-2 rounded-lg text-sm font-semibold transition-colors",
          disabled
            ? "bg-surface-3 text-text-muted cursor-not-allowed"
            : "bg-accent hover:bg-accent-hover text-white cursor-pointer"
        )}
      >
        Submit Decision
      </button>
    </div>
  );
}
