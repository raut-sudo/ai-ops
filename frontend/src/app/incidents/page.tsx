import { fetchIncidents } from "@/lib/api";
import { formatRelativeTime } from "@/lib/utils";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function IncidentsPage() {
  let incidents = [];
  let error: string | null = null;

  try {
    incidents = await fetchIncidents();
  } catch (e) {
    error = e instanceof Error ? e.message : "Failed to load incidents";
  }

  return (
    <div className="p-6 space-y-4">
      <h1 className="text-sm font-semibold text-text-primary">Incidents</h1>
      {error && (
        <p className="text-sm text-danger">{error}</p>
      )}
      {!error && incidents.length === 0 && (
        <p className="text-sm text-text-muted">No incidents recorded yet.</p>
      )}
      <div className="space-y-2">
        {incidents.map((incident) => (
          <Link
            key={incident.id}
            href={`/incidents/${incident.id}`}
            className="block border border-border rounded-lg p-4 hover:bg-surface-2 transition-colors"
          >
            <div className="flex items-start justify-between gap-3">
              <p className="text-sm text-text-primary flex-1">{incident.summary}</p>
              <span className="text-xs text-text-muted flex-shrink-0">
                {formatRelativeTime(incident.occurred_at)}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2">
              <span className="text-xs px-2 py-0.5 rounded bg-surface-3 text-text-secondary">
                {incident.status}
              </span>
              {incident.root_causes.slice(0, 2).map((rc, i) => (
                <span key={i} className="text-xs text-text-muted truncate">
                  {rc}
                </span>
              ))}
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
