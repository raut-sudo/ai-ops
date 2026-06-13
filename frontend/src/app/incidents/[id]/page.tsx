import { fetchIncident } from "@/lib/api";
import { formatRelativeTime } from "@/lib/utils";
import BeforeAfterPanel from "@/components/BeforeAfterPanel";
import Link from "next/link";
import { ChevronLeft } from "lucide-react";

export const dynamic = "force-dynamic";

interface Props {
  params: Promise<{ id: string }>;
}

export default async function IncidentDetailPage({ params }: Props) {
  const { id } = await params;
  let incident = null;
  let error: string | null = null;

  try {
    incident = await fetchIncident(id);
  } catch (e) {
    error = e instanceof Error ? e.message : "Failed to load incident";
  }

  return (
    <div className="p-6 space-y-6 max-w-3xl">
      <div className="flex items-center gap-2">
        <Link
          href="/incidents"
          className="flex items-center gap-1 text-xs text-text-muted hover:text-text-secondary transition-colors"
        >
          <ChevronLeft size={14} />
          Incidents
        </Link>
      </div>

      {error && <p className="text-sm text-danger">{error}</p>}

      {incident && (
        <>
          {/* Header */}
          <div className="space-y-1">
            <h1 className="text-base font-semibold text-text-primary">
              {incident.summary}
            </h1>
            <div className="flex items-center gap-2 text-xs text-text-muted">
              <span>{formatRelativeTime(incident.occurred_at)}</span>
              <span>·</span>
              <span className="px-2 py-0.5 rounded bg-surface-3 text-text-secondary">
                {incident.status}
              </span>
            </div>
          </div>

          {/* Root causes */}
          {incident.root_causes.length > 0 && (
            <section className="space-y-1">
              <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                Root Causes
              </h2>
              <ul className="list-disc list-inside space-y-0.5">
                {incident.root_causes.map((rc, i) => (
                  <li key={i} className="text-sm text-text-primary">
                    {rc}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Actions */}
          {incident.actions.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                Actions Taken
              </h2>
              <div className="space-y-2">
                {incident.actions.map((action) => (
                  <div
                    key={action.action_id}
                    className="border border-border rounded-lg p-3 flex items-start justify-between gap-3"
                  >
                    <div className="space-y-0.5">
                      <p className="text-sm font-mono text-text-primary">
                        {action.action_type}
                      </p>
                      <p className="text-xs text-text-muted">{action.target}</p>
                      {action.justification && (
                        <p className="text-xs text-text-secondary">
                          {action.justification}
                        </p>
                      )}
                    </div>
                    <span className="text-xs px-2 py-0.5 rounded bg-surface-3 text-text-secondary flex-shrink-0">
                      {action.status}
                    </span>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Before/After panel — §20.3 */}
          <BeforeAfterPanel actions={incident.actions} />

          {/* Outcome */}
          {incident.outcome && (
            <section className="space-y-1">
              <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                Outcome
              </h2>
              <p className="text-sm text-text-primary">{incident.outcome}</p>
            </section>
          )}
        </>
      )}
    </div>
  );
}
