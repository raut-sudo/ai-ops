"use client";

import { useEffect, useState } from "react";
import { ExternalLink, Activity } from "lucide-react";
import { getRecentSessions } from "@/lib/auth";
import { formatRelativeTime } from "@/lib/utils";

interface SessionEntry {
  thread_id: string;
  otel_trace_id: string | null;
  query: string;
  ts: string;
}

export default function ObservabilityClient() {
  const [sessions, setSessions] = useState<SessionEntry[]>([]);

  useEffect(() => {
    setSessions(getRecentSessions());
  }, []);

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <h1 className="text-sm font-semibold text-text-primary flex items-center gap-2">
        <Activity size={14} />
        Observability
      </h1>

      {/* External trace links */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Trace Destinations
        </h2>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <ExternalCard
            title="LangSmith"
            href="https://smith.langchain.com/"
            description="LangGraph runs — LANGCHAIN_TRACING_V2=true"
            hint="Set LANGCHAIN_API_KEY in backend/.env"
          />
          <ExternalCard
            title="Langfuse Cloud"
            href="https://cloud.langfuse.com/"
            description="OTLP traces by otel_trace_id"
            hint="Set LANGFUSE_SECRET_KEY + PUBLIC_KEY in backend/.env"
          />
        </div>
      </section>

      {/* Recent sessions from localStorage */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Recent Sessions (last {sessions.length})
        </h2>

        {sessions.length === 0 ? (
          <p className="text-sm text-text-muted">
            No sessions yet. Complete a chat query to populate this list.
          </p>
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => (
              <div
                key={s.thread_id}
                className="border border-border rounded-lg p-3 space-y-2"
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm text-text-primary truncate flex-1">
                    {s.query}
                  </p>
                  <span className="text-xs text-text-muted flex-shrink-0">
                    {formatRelativeTime(s.ts)}
                  </span>
                </div>

                <div className="flex flex-wrap gap-2">
                  {/* thread_id → LangSmith */}
                  <a
                    href={`https://smith.langchain.com/?thread_id=${encodeURIComponent(s.thread_id)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-xs text-accent hover:underline"
                  >
                    <ExternalLink size={11} />
                    LangSmith: {s.thread_id.slice(0, 16)}…
                  </a>

                  {/* otel_trace_id → Langfuse */}
                  {s.otel_trace_id && (
                    <a
                      href={`https://cloud.langfuse.com/traces/${encodeURIComponent(s.otel_trace_id)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 text-xs text-accent hover:underline"
                    >
                      <ExternalLink size={11} />
                      Langfuse: {s.otel_trace_id.slice(0, 16)}…
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Config notes */}
      <section className="space-y-2">
        <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
          Backend Config
        </h2>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <InfoCard hint="OTEL_EXPORTER_OTLP_ENDPOINT" value="http://localhost:4317" />
          <InfoCard hint="LANGCHAIN_TRACING_V2" value="true" />
          <InfoCard hint="Correlation ID header" value="X-Correlation-ID" />
          <InfoCard hint="Log format" value="JSON (structlog)" />
        </div>
      </section>
    </div>
  );
}

function ExternalCard({
  title,
  href,
  description,
  hint,
}: {
  title: string;
  href: string;
  description: string;
  hint: string;
}) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="block border border-border rounded-lg p-4 space-y-1 hover:bg-surface-2 transition-colors group"
    >
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-text-primary">{title}</p>
        <ExternalLink size={12} className="text-text-muted group-hover:text-accent transition-colors" />
      </div>
      <p className="text-xs text-text-secondary">{description}</p>
      <p className="text-xs text-text-muted font-mono">{hint}</p>
    </a>
  );
}

function InfoCard({ hint, value }: { hint: string; value: string }) {
  return (
    <div className="bg-surface-2 rounded-lg p-3 space-y-0.5">
      <p className="text-xs text-text-muted font-mono">{hint}</p>
      <p className="text-xs text-text-secondary">{value}</p>
    </div>
  );
}
