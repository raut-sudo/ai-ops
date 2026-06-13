/**
 * RTL exit-criteria tests for Sprint 8 (§24.1):
 *
 *  1. hitl_pending event → ApprovalCard renders with "Submit Decision" button
 *  2. final event → FinalResponseCard renders summary text
 *  3. State machine: idle → streaming → awaiting_approval → completed
 */

import React from "react";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom";

// ── Minimal test doubles ──────────────────────────────────────────────────────

// We test the Zustand store's FSM transitions directly by mocking fetch/stream.

jest.mock("@/lib/api", () => ({
  postChat: jest.fn(),
  postApprove: jest.fn(),
}));

jest.mock("@/lib/stream", () => ({
  parseNdjsonStream: jest.fn(),
}));

import { useChatStore } from "@/store/chatStore";
import { postChat, postApprove } from "@/lib/api";
import { parseNdjsonStream } from "@/lib/stream";
import ApprovalCard from "@/components/ApprovalCard";
import AgentTimeline from "@/components/AgentTimeline";
import type { ActionProposal, FinalResponse, ChatEvent } from "@/lib/types";

const MOCK_PROPOSAL: ActionProposal = {
  action_id: "act-001",
  action_type: "restock_product",
  target: "SKU-WIDGET-01",
  parameters: { action_type: "restock_product", sku: "SKU-WIDGET-01", quantity: 50 },
  risk_level: "low",
  justification: "Stock below reorder point",
  estimated_impact: "+50 units",
};

const MOCK_FINAL: FinalResponse = {
  session_id: "sess-001",
  query: "Why are sales down?",
  intent_type: "diagnostic",
  status: "success",
  summary: "Sales dropped due to stock-out of top SKU.",
  root_causes: [],
  domain_findings: {},
  memory_context: null,
  recommendations: ["Restock SKU-WIDGET-01"],
  proposed_actions: [],
  executed_actions: [],
  confidence_score: 0.92,
  low_confidence_flag: false,
  thread_id: "thread-001",
  langsmith_run_id: null,
  otel_trace_id: null,
  generated_at: new Date().toISOString(),
};

// ── Helper: reset Zustand store between tests ─────────────────────────────────

beforeEach(() => {
  useChatStore.getState().reset();
  jest.clearAllMocks();
});

// ── Test 1: hitl_pending → ApprovalCard renders Submit Decision ───────────────

describe("hitl_pending event", () => {
  it("renders ApprovalCard with Submit Decision button when status is awaiting_approval", async () => {
    // Simulate the store reaching awaiting_approval
    const mockEvents: ChatEvent[] = [
      { type: "hitl_pending", proposed_actions: [MOCK_PROPOSAL], thread_id: "thread-001" },
    ];

    async function* fakeStream() {
      for (const e of mockEvents) yield e;
    }

    (postChat as jest.Mock).mockResolvedValue({
      ok: true,
      body: {},
    });
    (parseNdjsonStream as jest.Mock).mockImplementation(() => fakeStream());

    await act(async () => {
      await useChatStore.getState().submitQuery("Why are sales down?");
    });

    expect(useChatStore.getState().status).toBe("awaiting_approval");
    expect(useChatStore.getState().proposedActions).toHaveLength(1);

    // Render ApprovalCard directly (component test)
    const onSubmit = jest.fn();
    render(
      <ApprovalCard
        proposals={[MOCK_PROPOSAL]}
        onSubmit={onSubmit}
      />
    );

    expect(screen.getByRole("button", { name: /submit decision/i })).toBeInTheDocument();
    expect(screen.getByText(/restock_product/i)).toBeInTheDocument();
  });
});

// ── Test 2: final event → FinalResponseCard renders summary ──────────────────

describe("final event", () => {
  it("renders final response summary text when status is completed", async () => {
    const mockEvents: ChatEvent[] = [
      { type: "final", final_response: MOCK_FINAL },
    ];

    async function* fakeStream() {
      for (const e of mockEvents) yield e;
    }

    (postChat as jest.Mock).mockResolvedValue({ ok: true, body: {} });
    (parseNdjsonStream as jest.Mock).mockImplementation(() => fakeStream());

    await act(async () => {
      await useChatStore.getState().submitQuery("Why are sales down?");
    });

    expect(useChatStore.getState().status).toBe("completed");
    expect(useChatStore.getState().finalResponse?.summary).toBe(MOCK_FINAL.summary);

    // Render AgentTimeline (includes FinalResponseCard)
    render(
      <AgentTimeline
        events={[{ type: "final", final_response: MOCK_FINAL }]}
        status="completed"
      />
    );

    expect(screen.getByText(MOCK_FINAL.summary)).toBeInTheDocument();
    expect(screen.getByText(/completed/i)).toBeInTheDocument();
  });
});

// ── Test 3: Full FSM round-trip: idle → streaming → awaiting_approval → completed

describe("state machine transitions", () => {
  it("transitions idle → streaming → awaiting_approval → completed", async () => {
    // Step 1: idle
    expect(useChatStore.getState().status).toBe("idle");

    // Mock streams
    const hitlEvents: ChatEvent[] = [
      { type: "hitl_pending", proposed_actions: [MOCK_PROPOSAL], thread_id: "thread-001" },
    ];
    async function* hitlStream() {
      for (const e of hitlEvents) yield e;
    }
    (postChat as jest.Mock).mockResolvedValue({ ok: true, body: {} });
    (parseNdjsonStream as jest.Mock).mockImplementation(() => hitlStream());
    (postApprove as jest.Mock).mockResolvedValue(MOCK_FINAL);

    // Step 2: submit query → streaming → awaiting_approval
    await act(async () => {
      await useChatStore.getState().submitQuery("What should we do about inventory?");
    });

    expect(useChatStore.getState().status).toBe("awaiting_approval");
    expect(useChatStore.getState().threadId).toBe("thread-001");

    // Step 3: submit approval → completed
    await act(async () => {
      await useChatStore.getState().submitApproval(["act-001"], []);
    });

    expect(useChatStore.getState().status).toBe("completed");
    expect(useChatStore.getState().finalResponse?.summary).toBe(MOCK_FINAL.summary);
  });
});
