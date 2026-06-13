/**
 * Zustand chat store — Finite State Machine (§20.2)
 *
 * States:
 *   idle → streaming        on submitQuery
 *   streaming → awaiting_approval   on hitl_pending event
 *   streaming → completed           on final event
 *   streaming → error               on error event
 *   awaiting_approval → completed   on submitApproval
 */

import { create } from "zustand";
import type {
  ChatEvent,
  DomainFinding,
  SynthesisResult,
  ActionProposal,
  FinalResponse,
} from "@/lib/types";
import { postChat, postApprove } from "@/lib/api";
import { parseNdjsonStream } from "@/lib/stream";
import { getUserId, recordSession } from "@/lib/auth";

// ── helpers ──────────────────────────────────────────────────────────────────

function newUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // Fallback for Jest / Node.js 18 environments without crypto.randomUUID
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

// ── types ────────────────────────────────────────────────────────────────────

export type ChatStatus =
  | "idle"
  | "streaming"
  | "awaiting_approval"
  | "completed"
  | "error";

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
  ts: string;
}

export interface ChatStore {
  status: ChatStatus;
  query: string;
  events: ChatEvent[];
  domainFindings: DomainFinding[];
  synthesis: SynthesisResult | null;
  proposedActions: ActionProposal[];
  /** HITL approval thread id — from hitl_pending event. */
  threadId: string | null;
  finalResponse: FinalResponse | null;
  error: string | null;
  /**
   * Sticky conversation thread id for multi-turn chat.
   * Generated on first submitQuery; cleared by reset().
   * Sent as thread_id with every postChat so the backend appends to the
   * same LangGraph checkpoint instead of starting a fresh thread.
   */
  sessionThreadId: string | null;
  /**
   * Human-readable conversation history for display in the UI.
   * Grows with each turn; cleared by reset().
   */
  conversationMessages: ConversationMessage[];

  // Actions
  submitQuery: (query: string) => Promise<void>;
  submitApproval: (approvedIds: string[], rejectedIds: string[]) => Promise<void>;
  reset: () => void;
}

const initialState = {
  status: "idle" as ChatStatus,
  query: "",
  events: [] as ChatEvent[],
  domainFindings: [] as DomainFinding[],
  synthesis: null,
  proposedActions: [] as ActionProposal[],
  threadId: null,
  finalResponse: null,
  error: null,
  sessionThreadId: null,
  conversationMessages: [] as ConversationMessage[],
};

export const useChatStore = create<ChatStore>((set, get) => ({
  ...initialState,

  submitQuery: async (query: string) => {
    // Preserve sessionThreadId and conversationMessages across turns.
    const prevSessionThreadId = get().sessionThreadId;
    const prevConversationMessages = get().conversationMessages;

    // Generate a new session thread id on first turn, then reuse it.
    const sessionThreadId = prevSessionThreadId ?? newUuid();

    // Append the user's message immediately for responsive UI.
    const userMessage: ConversationMessage = {
      role: "user",
      content: query,
      ts: new Date().toISOString(),
    };

    set({
      ...initialState,
      status: "streaming",
      query,
      sessionThreadId,
      conversationMessages: [...prevConversationMessages, userMessage],
    });

    try {
      const response = await postChat(query, sessionThreadId);

      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: response.statusText }));
        set({
          status: "error",
          error: (body as { detail?: string }).detail ?? "Chat request failed",
        });
        return;
      }

      for await (const event of parseNdjsonStream(response)) {
        set((state) => ({ events: [...state.events, event] }));

        switch (event.type) {
          case "domain_finding":
            set((state) => ({
              domainFindings: [...state.domainFindings, event.finding],
            }));
            break;

          case "synthesis":
            set({ synthesis: event.synthesis });
            break;

          case "hitl_pending":
            set({
              status: "awaiting_approval",
              proposedActions: event.proposed_actions,
              threadId: event.thread_id,
            });
            return; // Terminal — stop iterating

          case "final": {
            const summary = event.final_response?.summary ?? "";
            const assistantMessage: ConversationMessage | null = summary
              ? { role: "assistant", content: summary, ts: new Date().toISOString() }
              : null;

            set((state) => ({
              status: "completed",
              finalResponse: event.final_response,
              conversationMessages: assistantMessage
                ? [...state.conversationMessages, assistantMessage]
                : state.conversationMessages,
            }));
            recordSession({
              thread_id: event.final_response.thread_id,
              otel_trace_id: event.final_response.otel_trace_id ?? null,
              query: get().query,
              ts: new Date().toISOString(),
            });
            return; // Terminal
          }

          case "error":
            set({ status: "error", error: event.message });
            return; // Terminal
        }
      }

      // Stream ended without a terminal event (should not happen per §30.11)
      if (get().status === "streaming") {
        set({ status: "error", error: "Stream ended without a terminal event" });
      }
    } catch (err) {
      set({
        status: "error",
        error: err instanceof Error ? err.message : "Unknown error",
      });
    }
  },

  submitApproval: async (approvedIds: string[], rejectedIds: string[]) => {
    const { threadId } = get();
    if (!threadId) return;

    try {
      const decision = {
        approved_action_ids: approvedIds,
        rejected_action_ids: rejectedIds,
        approver: getUserId(),
      };

      const finalResponse = await postApprove(threadId, decision);
      if (finalResponse.thread_id) {
        recordSession({
          thread_id: finalResponse.thread_id,
          otel_trace_id: finalResponse.otel_trace_id ?? null,
          query: get().query,
          ts: new Date().toISOString(),
        });
      }
      set({ status: "completed", finalResponse });
    } catch (err) {
      set({
        status: "error",
        error: err instanceof Error ? err.message : "Approval failed",
      });
    }
  },

  reset: () => set({ ...initialState }),
}));
