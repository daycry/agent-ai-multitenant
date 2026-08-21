import { describe, expect, it } from "vitest";

import { conversationLabel, nextActiveAfterDelete } from "./conversation-history";

const conv = (id: string, title: string | null, created_at = "2026-06-20T10:00:00Z") => ({
  id,
  title,
  current_mode: "planning",
  created_at,
});

describe("nextActiveAfterDelete", () => {
  it("selects the most recent remaining (last in the ascending list)", () => {
    const list = [conv("a", "A"), conv("b", "B"), conv("c", "C")];
    expect(nextActiveAfterDelete(list, "c")).toBe("b"); // deleted the last → previous
    expect(nextActiveAfterDelete(list, "a")).toBe("c"); // deleted the first → newest remains
  });

  it("returns null when the deleted one was the only conversation", () => {
    expect(nextActiveAfterDelete([conv("only", "X")], "only")).toBeNull();
  });

  it("ignores a deletedId that isn't in the list and keeps the newest", () => {
    const list = [conv("a", "A"), conv("b", "B")];
    expect(nextActiveAfterDelete(list, "ghost")).toBe("b");
  });
});

// `lang` pasó a ser un parámetro OBLIGATORIO en prod-16 `task_prod16_03`: el
// helper pintaba dos textos castellanos fijos en el selector del historial, así
// que el panel en inglés los enseñaba en castellano. Sin default a propósito —
// un default deja que un llamante nuevo se olvide y reintroduzca el fallo.
describe("conversationLabel", () => {
  it("prefers a non-empty title", () => {
    expect(conversationLabel(conv("a", "Plan de reservas"), "es")).toBe("Plan de reservas");
    // El título es un DATO del usuario: no se traduce en ningún idioma.
    expect(conversationLabel(conv("a", "Plan de reservas"), "en")).toBe("Plan de reservas");
  });

  it("falls back to a date stamp for an untitled conversation", () => {
    const label = conversationLabel(conv("a", null, "2026-06-20T10:00:00Z"), "es");
    expect(label.startsWith("Conversación · ")).toBe(true);
    expect(label).not.toBe("Conversación · ");
  });

  it("uses the English stamp when the panel is in English", () => {
    const label = conversationLabel(conv("a", null, "2026-06-20T10:00:00Z"), "en");
    expect(label.startsWith("Conversation · ")).toBe(true);
    expect(label).not.toContain("Conversación");
  });

  it("falls back to a safe label when created_at is unparseable", () => {
    expect(conversationLabel(conv("a", "   ", "not-a-date"), "es")).toBe("Conversación sin título");
    expect(conversationLabel(conv("a", "   ", "not-a-date"), "en")).toBe("Untitled conversation");
  });
});
