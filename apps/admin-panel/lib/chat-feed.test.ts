import { describe, expect, it } from "vitest";

import {
  CHAT_POLL_MS,
  PLANNING_INFLIGHT_WINDOW_MS,
  chatRefetchInterval,
  isReplyInFlight,
  type FeedMessage,
} from "@/lib/chat-feed";

const NOW = Date.parse("2026-06-22T12:00:00.000Z");

function msg(over: Partial<FeedMessage>): FeedMessage {
  return {
    author_kind: "agent",
    mode: "planning",
    created_at: new Date(NOW).toISOString(),
    attachments: [],
    ...over,
  };
}

describe("isReplyInFlight", () => {
  it("is not in flight without messages", () => {
    expect(isReplyInFlight([], NOW)).toBe(false);
    expect(isReplyInFlight(undefined, NOW)).toBe(false);
  });

  it("is in flight while the last message is the user's (awaiting first reply)", () => {
    expect(isReplyInFlight([msg({ author_kind: "user" })], NOW)).toBe(true);
  });

  it("STAYS in flight after the first agent message of a planning turn (regression)", () => {
    // The PM framing arrived, but the specialists + synthesis are still coming —
    // the safety-net poll must NOT stop here (this was the bug).
    const recent = new Date(NOW - 30_000).toISOString();
    expect(isReplyInFlight([msg({ author_kind: "agent", created_at: recent })], NOW)).toBe(true);
  });

  it("settles once the planning synthesis presents a plan (carries an attachment)", () => {
    expect(isReplyInFlight([msg({ attachments: [{ type: "plan_draft" }] })], NOW)).toBe(false);
  });

  it("settles when the last planning message is older than the in-flight window", () => {
    const stale = new Date(NOW - PLANNING_INFLIGHT_WINDOW_MS - 1000).toISOString();
    expect(isReplyInFlight([msg({ created_at: stale })], NOW)).toBe(false);
  });

  it("settles immediately for a single-reply mode once the agent answers", () => {
    const recent = new Date(NOW - 5_000).toISOString();
    expect(
      isReplyInFlight([msg({ mode: "discussion", author_kind: "agent", created_at: recent })], NOW),
    ).toBe(false);
  });
});

describe("chatRefetchInterval", () => {
  it("polls while in flight and stops otherwise", () => {
    expect(chatRefetchInterval([msg({ author_kind: "user" })], NOW)).toBe(CHAT_POLL_MS);
    expect(chatRefetchInterval([msg({ attachments: [{ x: 1 }] })], NOW)).toBe(false);
  });
});
