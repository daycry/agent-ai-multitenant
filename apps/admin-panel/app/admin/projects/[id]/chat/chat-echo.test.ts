import { describe, expect, it } from "vitest";

import {
  appendUnlessPresent,
  echoToMessage,
  mergePendingEchoes,
  nextEchoId,
  type PendingEcho,
} from "@/app/admin/projects/[id]/chat/chat-echo";
import type { Message } from "@/app/admin/projects/[id]/chat/chat-types";

function persisted(over: Partial<Message> = {}): Message {
  return {
    id: "m-1",
    tenant_id: "t-1",
    conversation_id: "conv-1",
    author_kind: "user",
    author_user_id: "u-1",
    author_agent_id: null,
    content: "hola",
    mode: "planning",
    attachments: [],
    related_plan_id: null,
    is_summary: false,
    created_at: "2026-08-29T09:00:00Z",
    ...over,
  };
}

function echo(over: Partial<PendingEcho> = {}): PendingEcho {
  return {
    tempId: nextEchoId(),
    conversationId: "conv-1",
    content: "hola",
    mode: "planning",
    createdAt: "2026-08-29T09:00:01Z",
    seenIds: [],
    ...over,
  };
}

describe("nextEchoId", () => {
  it("no puede colisionar con un id del servidor y no se repite", () => {
    const a = nextEchoId();
    const b = nextEchoId();
    expect(a).not.toBe(b);
    // Los ids del servidor son UUID: el prefijo hace imposible confundirlos, que
    // es lo que permite deduplicar por id sin miedo.
    expect(a.startsWith("optimistic:")).toBe(true);
  });
});

describe("appendUnlessPresent", () => {
  it("añade el mensaje que aún no está", () => {
    expect(appendUnlessPresent([], persisted({ id: "m-9" })).map((m) => m.id)).toEqual(["m-9"]);
    expect(appendUnlessPresent(undefined, persisted({ id: "m-9" })).map((m) => m.id)).toEqual([
      "m-9",
    ]);
  });

  it("NO lo añade dos veces cuando el WebSocket se adelantó al POST (H7)", () => {
    const already = [persisted({ id: "m-9" })];
    expect(appendUnlessPresent(already, persisted({ id: "m-9" })).map((m) => m.id)).toEqual([
      "m-9",
    ]);
  });
});

describe("mergePendingEchoes", () => {
  it("pinta el eco mientras el mensaje no ha vuelto del servidor", () => {
    const merged = mergePendingEchoes([], [echo({ tempId: "optimistic:1", content: "hola" })]);
    expect(merged.map((m) => m.id)).toEqual(["optimistic:1"]);
    expect(merged[0].author_kind).toBe("user");
    expect(merged[0].content).toBe("hola");
  });

  it("el eco DESAPARECE cuando llega su persistido: sustituye, no suma (H7)", () => {
    const merged = mergePendingEchoes(
      [persisted({ id: "m-9", content: "hola" })],
      [echo({ tempId: "optimistic:1", content: "hola", seenIds: [] })],
    );
    expect(merged.map((m) => m.id)).toEqual(["m-9"]);
  });

  it("un mensaje ANTERIOR con el mismo texto no cancela el eco nuevo", () => {
    // El caso que rompe una deduplicación por contenido a secas: el usuario
    // repite la misma frase. `seenIds` es lo que distingue «ya estaba» de
    // «acaba de llegar».
    const merged = mergePendingEchoes(
      [persisted({ id: "m-1", content: "otra vez" })],
      [echo({ tempId: "optimistic:2", content: "otra vez", seenIds: ["m-1"] })],
    );
    expect(merged.map((m) => m.id)).toEqual(["m-1", "optimistic:2"]);
  });

  it("dos ecos iguales en vuelo se reconcilian de uno en uno", () => {
    const pending = [
      echo({ tempId: "optimistic:1", content: "otra vez", seenIds: [] }),
      echo({ tempId: "optimistic:2", content: "otra vez", seenIds: [] }),
    ];
    // Llega el primero: cancela UN eco, no los dos.
    const half = mergePendingEchoes([persisted({ id: "m-1", content: "otra vez" })], pending);
    expect(half.map((m) => m.id)).toEqual(["m-1", "optimistic:2"]);

    // Llega el segundo: ya no queda eco que pintar.
    const done = mergePendingEchoes(
      [
        persisted({ id: "m-1", content: "otra vez" }),
        persisted({ id: "m-2", content: "otra vez" }),
      ],
      pending,
    );
    expect(done.map((m) => m.id)).toEqual(["m-1", "m-2"]);
  });

  it("un mensaje de AGENTE con el mismo texto no cancela el eco del usuario", () => {
    const merged = mergePendingEchoes(
      [persisted({ id: "m-9", author_kind: "agent", content: "hola" })],
      [echo({ tempId: "optimistic:1", content: "hola" })],
    );
    expect(merged.map((m) => m.id)).toEqual(["m-9", "optimistic:1"]);
  });

  it("sin ecos devuelve el feed tal cual", () => {
    const feed = [persisted({ id: "m-1" }), persisted({ id: "m-2" })];
    expect(mergePendingEchoes(feed, [])).toEqual(feed);
  });
});

describe("echoToMessage", () => {
  it("rellena la forma de Message sin inventarse autoría de agente", () => {
    const msg = echoToMessage(echo({ tempId: "optimistic:7", mode: "discussion" }));
    expect(msg.id).toBe("optimistic:7");
    expect(msg.author_kind).toBe("user");
    expect(msg.author_agent_id).toBeNull();
    expect(msg.mode).toBe("discussion");
    expect(msg.is_summary).toBe(false);
    expect(msg.attachments).toEqual([]);
  });
});
