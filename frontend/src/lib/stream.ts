import type { ChatEvent } from "@/lib/types";

/**
 * Reads an NDJSON (newline-delimited JSON) stream from a Response body
 * and yields parsed ChatEvent objects.
 *
 * The stream terminates with exactly one terminal event:
 *   "hitl_pending" | "final" | "error"  (§30.11)
 */
export async function* parseNdjsonStream(
  response: Response
): AsyncGenerator<ChatEvent> {
  if (!response.body) {
    throw new Error("Response has no body");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");

      // Keep the last (potentially incomplete) line in the buffer
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const event = JSON.parse(trimmed) as ChatEvent;
          yield event;
        } catch {
          // Malformed line — skip
        }
      }
    }

    // Flush any remaining buffer content
    if (buffer.trim()) {
      try {
        const event = JSON.parse(buffer.trim()) as ChatEvent;
        yield event;
      } catch {
        // Ignore incomplete final chunk
      }
    }
  } finally {
    reader.releaseLock();
  }
}
