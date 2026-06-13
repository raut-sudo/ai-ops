"use client";

import { useEffect, useState } from "react";
import { Package, TrendingUp, TrendingDown, ArrowRight } from "lucide-react";
import { fetchStock } from "@/lib/api";
import type { ActionRecord, StockLevel } from "@/lib/types";

interface BeforeAfterPanelProps {
  actions: ActionRecord[];
}

interface StockPair {
  action: ActionRecord;
  beforeQty: number | null; // from action.parameters at proposal time
  after: StockLevel | null;
  loading: boolean;
  error: string | null;
}

export default function BeforeAfterPanel({ actions }: BeforeAfterPanelProps) {
  const restockActions = actions.filter(
    (a) => a.action_type === "restock_product" && a.status === "executed"
  );

  const [pairs, setPairs] = useState<StockPair[]>(
    restockActions.map((a) => {
      // Extract "before" quantity from parameters recorded at proposal time
      const params = (a as unknown as { parameters?: Record<string, unknown> }).parameters;
      const beforeQty =
        typeof params?.quantity_before === "number"
          ? params.quantity_before
          : null;
      return { action: a, beforeQty, after: null, loading: true, error: null };
    })
  );

  useEffect(() => {
    if (restockActions.length === 0) return;

    restockActions.forEach((action, idx) => {
      const sku = action.target;
      fetchStock(sku)
        .then((stock) => {
          setPairs((prev) =>
            prev.map((p, i) =>
              i === idx ? { ...p, after: stock, loading: false } : p
            )
          );
        })
        .catch((err: unknown) => {
          setPairs((prev) =>
            prev.map((p, i) =>
              i === idx
                ? {
                    ...p,
                    loading: false,
                    error: err instanceof Error ? err.message : "Load failed",
                  }
                : p
            )
          );
        });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (restockActions.length === 0) return null;

  return (
    <section className="space-y-3">
      <h2 className="text-xs font-semibold text-text-secondary uppercase tracking-wide flex items-center gap-2">
        <Package size={14} />
        Closed-Loop: Stock Before / After (§20.3)
      </h2>
      {pairs.map(({ action, beforeQty, after, loading, error }) => (
        <div
          key={action.action_id}
          className="border border-border rounded-xl p-4 space-y-3"
        >
          {/* SKU header + proof link */}
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-mono text-text-primary">{action.target}</p>
            <span className="text-xs px-2 py-0.5 rounded bg-accent/10 text-accent font-mono">
              reference_id: {action.action_id}
            </span>
          </div>

          {loading && (
            <p className="text-xs text-text-muted animate-pulse">Fetching live stock…</p>
          )}
          {error && <p className="text-xs text-danger">{error}</p>}

          {/* Before / After side-by-side */}
          {(beforeQty !== null || after) && (
            <div className="flex items-center gap-3">
              {/* Before */}
              <div className="flex-1 bg-surface-2 rounded-lg p-3 text-center">
                <p className="text-xs text-text-muted mb-1">Before</p>
                <p className="text-xl font-bold text-text-primary">
                  {beforeQty !== null ? beforeQty : "—"}
                </p>
                <p className="text-xs text-text-muted">units</p>
              </div>

              <ArrowRight size={18} className="text-text-muted flex-shrink-0" />

              {/* After */}
              <div className="flex-1 bg-surface-2 rounded-lg p-3 text-center">
                <p className="text-xs text-text-muted mb-1">After</p>
                {after ? (
                  <>
                    <p className="text-xl font-bold text-success">
                      {after.quantity_on_hand}
                    </p>
                    <p className="text-xs text-text-muted">units on hand</p>
                  </>
                ) : (
                  <p className="text-xl font-bold text-text-primary">—</p>
                )}
              </div>

              {/* Delta */}
              {beforeQty !== null && after && (
                <DeltaBadge
                  delta={after.quantity_on_hand - beforeQty}
                />
              )}
            </div>
          )}

          {/* Extra stock details */}
          {after && (
            <div className="grid grid-cols-2 gap-2">
              <Metric label="Reserved" value={after.quantity_reserved} />
              <Metric label="Reorder Point" value={after.reorder_point} />
            </div>
          )}
        </div>
      ))}
    </section>
  );
}

function DeltaBadge({ delta }: { delta: number }) {
  const positive = delta > 0;
  return (
    <div
      className={`flex flex-col items-center justify-center px-2 py-1 rounded-lg ${
        positive ? "bg-success/10 text-success" : "bg-danger/10 text-danger"
      }`}
    >
      {positive ? (
        <TrendingUp size={14} />
      ) : (
        <TrendingDown size={14} />
      )}
      <span className="text-xs font-bold">
        {positive ? "+" : ""}
        {delta}
      </span>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-surface-2 rounded p-2">
      <p className="text-xs text-text-muted">{label}</p>
      <p className="text-sm font-semibold text-text-primary">{value}</p>
    </div>
  );
}
