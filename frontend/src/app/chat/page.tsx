"use client";

import { useChatStore } from "@/store/chatStore";
import AgentTimeline from "@/components/AgentTimeline";
import ApprovalCard from "@/components/ApprovalCard";
import ChatInput from "@/components/ChatInput";
import type { ConversationMessage } from "@/store/chatStore";
import clsx from "clsx";

// ── Conversation history bubble ───────────────────────────────────────────────

function MessageBubble({ msg }: { msg: ConversationMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={clsx("flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={clsx(
          "max-w-[75%] rounded-2xl px-4 py-2 text-sm",
          isUser
            ? "bg-accent text-white rounded-br-sm"
            : "bg-surface border border-border text-text-primary rounded-bl-sm"
        )}
      >
        {msg.content}
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ChatPage() {
  const {
    status,
    events,
    proposedActions,
    conversationMessages,
    submitQuery,
    submitApproval,
    reset,
  } = useChatStore();

  const isStreaming = status === "streaming";
  const awaitingApproval = status === "awaiting_approval";

  return (
    <div className="flex flex-col h-screen">
      {/* Header */}
      <div className="px-6 py-4 border-b border-border flex items-center justify-between">
        <h1 className="text-sm font-semibold text-text-primary">Chat</h1>
        {status !== "idle" && (
          <button
            onClick={reset}
            className="text-xs text-text-muted hover:text-text-secondary transition-colors"
          >
            New chat
          </button>
        )}
      </div>

      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {status === "idle" && conversationMessages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full gap-2 text-center">
            <p className="text-lg font-semibold text-text-primary">
              AI Operations Brain
            </p>
            <p className="text-sm text-text-muted max-w-sm">
              Ask about sales, inventory, marketing, or support issues. The agent
              will diagnose, synthesise, and propose actions.
            </p>
          </div>
        ) : (
          <div className="space-y-4 max-w-2xl mx-auto">
            {/* Conversation history (prior turns) */}
            {conversationMessages.length > 0 && (
              <div className="space-y-2 pb-2">
                {conversationMessages.map((msg, i) => (
                  <MessageBubble key={i} msg={msg} />
                ))}
              </div>
            )}

            {/* Agent timeline for the current in-flight turn */}
            {status !== "idle" && (
              <AgentTimeline events={events} status={status} />
            )}

            {awaitingApproval && proposedActions.length > 0 && (
              <ApprovalCard
                proposals={proposedActions}
                onSubmit={submitApproval}
              />
            )}
          </div>
        )}
      </div>

      {/* Input — pinned bottom */}
      <div className="px-6 pb-6 pt-3 border-t border-border max-w-2xl w-full mx-auto">
        <ChatInput
          onSubmit={submitQuery}
          disabled={isStreaming || awaitingApproval}
          placeholder={
            awaitingApproval
              ? "Approve or reject proposed actions above…"
              : "Ask about sales, inventory, marketing, or support…"
          }
        />
        {isStreaming && (
          <p className="text-xs text-text-muted text-center mt-2 animate-pulse">
            Agent thinking…
          </p>
        )}
      </div>
    </div>
  );
}
