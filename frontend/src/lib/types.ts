// TypeScript interfaces mirroring backend Pydantic schemas.
// Keep in sync with backend/app/schemas/__init__.py.

export type Domain = "sales" | "inventory" | "marketing" | "support";

export interface MetricSnapshot {
  name: string;
  value: number | string;
  unit: string;
  period: string;
  delta_pct?: number | null;
}

export interface DomainFinding {
  domain: Domain;
  findings: string[];
  metrics: MetricSnapshot[];
  anomalies: string[];
  confidence: number;
  tool_calls_made: string[];
  severity: "low" | "medium" | "high" | "critical";
}

export interface PastIncident {
  incident_id: string;
  occurred_at: string;
  summary: string;
  root_causes: string[];
  actions_taken: string[];
  outcome?: string | null;
  similarity_score: number;
}

export interface MemoryContext {
  past_incidents: PastIncident[];
  recommended_actions_from_history: string[];
  relevant_outcomes: string[];
}

export interface RootCause {
  cause: string;
  domain: string;
  evidence: string[];
  confidence: number;
}

export interface SynthesisResult {
  correlated_explanation: string;
  root_causes: RootCause[];
  contributing_factors: Record<string, string>;
  confidence_score: number;
  recommendations: string[];
  domains_correlated: string[];
}

// ActionParams (discriminated union — mirrored simply)
export type ActionParams =
  | { action_type: "restock_product"; sku: string; quantity: number }
  | { action_type: "apply_discount"; sku: string; percent: number }
  | { action_type: "pause_campaign" | "activate_campaign"; campaign_id: string }
  | { action_type: "create_support_ticket"; subject: string; priority: "low" | "medium" | "high" }
  | { action_type: "send_alert"; channel: string; message: string };

export interface ActionProposal {
  action_id: string;
  action_type: string; // explicitly serialized by backend (§30.13)
  target: string;
  parameters: ActionParams;
  risk_level: "low" | "medium" | "high";
  justification: string;
  estimated_impact: string;
}

export interface HITLDecision {
  approved_action_ids: string[];
  rejected_action_ids: string[];
  approver: string;
  rejection_reason?: string | null;
  decided_at?: string;
}

export interface ActionResult {
  action_id: string;
  status: "executed" | "failed" | "skipped";
  result_payload: Record<string, unknown>;
  error?: string | null;
  executed_at: string;
}

export interface FinalResponse {
  session_id: string;
  query: string;
  intent_type: string;
  status: "success" | "low_confidence" | "hitl_pending" | "error" | "irrelevant";
  summary: string;
  root_causes: RootCause[];
  domain_findings: Record<string, DomainFinding>;
  memory_context?: MemoryContext | null;
  recommendations: string[];
  proposed_actions: ActionProposal[];
  executed_actions: ActionResult[];
  confidence_score: number;
  low_confidence_flag: boolean;
  thread_id: string;
  langsmith_run_id?: string | null;
  otel_trace_id?: string | null;
  generated_at: string;
}

// ── Stream event types (§19.3) ─────────────────────────────────────────────

export type ChatEvent =
  | { type: "node_start"; node: string; ts: string }
  | { type: "domain_finding"; domain: string; finding: DomainFinding }
  | { type: "synthesis"; synthesis: SynthesisResult }
  | { type: "hitl_pending"; proposed_actions: ActionProposal[]; thread_id: string }
  | { type: "final"; final_response: FinalResponse }
  | { type: "error"; message: string };

// ── API response types ──────────────────────────────────────────────────────

export interface IncidentSummary {
  id: string;
  summary: string;
  status: string;
  occurred_at: string;
  root_causes: string[];
  actions_taken: string[];
}

export interface ActionRecord {
  action_id: string;
  action_type: string;
  target: string;
  status: string;
  risk_level: string;
  justification?: string | null;
  created_at: string;
}

export interface IncidentDetail extends IncidentSummary {
  outcome?: string | null;
  actions: ActionRecord[];
}

export interface PendingAction {
  action_id: string;
  session_id: string;
  action_type: string;
  target: string;
  parameters: Record<string, unknown>;
  risk_level: string;
  justification?: string | null;
  created_at: string;
}

export interface StockLevel {
  sku: string;
  quantity_on_hand: number;
  quantity_reserved: number;
  reorder_point: number;
  reorder_quantity: number;
  warehouse_id: string;
  updated_at: string;
}
