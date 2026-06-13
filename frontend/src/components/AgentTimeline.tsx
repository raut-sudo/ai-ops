"use client";

import { cn } from "@/lib/utils";
import type { ChatEvent, DomainFinding, SynthesisResult, FinalResponse } from "@/lib/types";

interface AgentTimelineProps {
  events: ChatEvent[];
  status: string;
}

function NodeStartRow({ node }: { node: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-text-muted py-1">
      <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
      Running <span className="text-text-secondary font-mono">{node}</span>…
    </div>
  );
}

function DomainFindingRow({ domain, finding }: { domain: string; finding: DomainFinding }) {
  const severityColor: Record<string, string> = {
    critical: "text-danger",
    high: "text-warning",
    medium: "text-yellow-400",
    low: "text-success",
  };
  return (
    <div className="border border-border rounded-lg p-3 space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          {domain}
        </span>
        <span className={cn("text-xs font-medium", severityColor[finding.severity] ?? "text-text-muted")}>
          {finding.severity}
        </span>
      </div>
      <ul className="space-y-0.5">
        {finding.findings.map((f, i) => (
          <li key={i} className="text-sm text-text-primary">
            {f}
          </li>
        ))}
      </ul>
    </div>
  );
}

function SynthesisRow({ synthesis }: { synthesis: SynthesisResult }) {
  return (
    <div className="border border-border rounded-lg p-3 space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
        Synthesis
      </p>
      <p className="text-sm text-text-primary">{synthesis.correlated_explanation}</p>
      {synthesis.root_causes.length > 0 && (
        <ul className="space-y-1">
          {synthesis.root_causes.map((rc, i) => (
            <li key={i} className="text-xs text-text-secondary">
              <span className="text-text-primary font-medium">{rc.cause}</span>
              {" — "}
              {rc.domain}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FinalResponseCard({ fr }: { fr: FinalResponse }) {
  return (
    <div className="border border-border rounded-lg p-4 space-y-3">
      <p className="text-xs font-semibold uppercase tracking-wide text-success">
        Completed
      </p>
      <p className="text-sm text-text-primary">{fr.summary}</p>
      {fr.recommendations.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs text-text-muted">Recommendations</p>
          <ul className="list-disc list-inside space-y-0.5">
            {fr.recommendations.map((r, i) => (
              <li key={i} className="text-sm text-text-secondary">
                {r}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function AgentTimeline({ events, status }: AgentTimelineProps) {
  if (events.length === 0 && status === "idle") return null;

  return (
    <div className="space-y-2 w-full max-w-2xl mx-auto">
      {events.map((event, i) => {
        switch (event.type) {
          case "node_start":
            return <NodeStartRow key={i} node={event.node} />;
          case "domain_finding":
            return <DomainFindingRow key={i} domain={event.domain} finding={event.finding} />;
          case "synthesis":
            return <SynthesisRow key={i} synthesis={event.synthesis} />;
          case "final":
            return <FinalResponseCard key={i} fr={event.final_response} />;
          case "error":
            return (
              <div key={i} className="border border-danger rounded-lg p-3 text-sm text-danger">
                {event.message}
              </div>
            );
          default:
            return null;
        }
      })}
    </div>
  );
}
