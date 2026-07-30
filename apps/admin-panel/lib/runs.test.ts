import { describe, expect, it } from "vitest";

import {
  fmtRunDuration,
  fmtRunMoney,
  fmtRunTokens,
  fmtRunWhen,
  runsQuery,
  runStatusLabel,
  runStatusVariant,
} from "@/lib/runs";

describe("runsQuery", () => {
  it("is empty with no filters", () => {
    expect(runsQuery()).toBe("");
    expect(runsQuery({})).toBe("");
  });

  it("includes set string filters", () => {
    expect(runsQuery({ task_id: "t1" })).toBe("?task_id=t1");
  });

  it("drops undefined / null / blank values", () => {
    expect(runsQuery({ task_id: "", verdict: undefined, model: "   " })).toBe("");
  });

  it("serializes numbers", () => {
    const qs = runsQuery({ limit: 50, min_cost: 0.5 });
    expect(qs).toContain("limit=50");
    expect(qs).toContain("min_cost=0.5");
  });

  it("keeps only the non-empty filters", () => {
    expect(runsQuery({ task_id: "abc", plan_id: "" })).toBe("?task_id=abc");
  });
});

describe("runStatusVariant", () => {
  it("maps the human-attention states to warning, not the muted fallback (F49/F50)", () => {
    // The bug: these statuses were absent from the map → silently `muted`.
    expect(runStatusVariant("needs_human_review")).toBe("warning");
    expect(runStatusVariant("awaiting_human_approval")).toBe("warning");
  });

  it("maps the terminal states", () => {
    expect(runStatusVariant("done")).toBe("success");
    expect(runStatusVariant("failed")).toBe("danger");
    expect(runStatusVariant("aborted")).toBe("warning");
    expect(runStatusVariant("cancelled")).toBe("muted");
    expect(runStatusVariant("running")).toBe("info");
  });

  it("falls back to muted for an unknown status", () => {
    expect(runStatusVariant("totally_unknown")).toBe("muted");
  });
});

describe("runStatusLabel", () => {
  it("returns readable Spanish labels for the persistible statuses", () => {
    expect(runStatusLabel("done")).toBe("Completado");
    expect(runStatusLabel("running")).toBe("En curso");
    expect(runStatusLabel("awaiting_human_approval")).toBe("Esperando aprobación");
    expect(runStatusLabel("needs_human_review")).toBe("Revisión humana");
    expect(runStatusLabel("cancelled")).toBe("Cancelado");
  });

  it("falls back to the raw status when unmapped", () => {
    expect(runStatusLabel("weird")).toBe("weird");
  });
});

// --- B2 (runs-visor): formateo compartido de tokens / coste / duración -------

function row(overrides: Partial<import("@/lib/runs").ExecutionRunRow> = {}) {
  return {
    id: "r1",
    created_at: "2026-07-08T10:00:00Z",
    task_id: "t1",
    task_title: "T",
    plan_id: null,
    plan_title: null,
    agent_id: null,
    agent_name: null,
    agent_role: null,
    model: null,
    verdict: "done",
    succeeded: true,
    finish_status: null,
    retry_count: 0,
    duration_ms: 1500,
    total_tokens: 1234,
    total_cost_usd: "0.1234",
    started_at: null,
    completed_at: null,
    display_currency: null,
    display_cost: null,
    applied_rate: null,
    applied_rate_date: null,
    ...overrides,
  } satisfies import("@/lib/runs").ExecutionRunRow;
}

describe("fmtRunMoney", () => {
  it("renders canonical USD with $ and 4 decimals", () => {
    expect(fmtRunMoney(row())).toBe("$0.1234");
  });

  it("prefers the tenant display currency when set", () => {
    expect(fmtRunMoney(row({ display_currency: "EUR", display_cost: "0.1100" }))).toBe(
      "0.1100 EUR",
    );
  });

  it("shows an em dash for a zero cost (run still running)", () => {
    expect(fmtRunMoney(row({ total_cost_usd: "0" }))).toBe("—");
  });
});

describe("fmtRunDuration", () => {
  it("uses seconds above 1s and ms below", () => {
    expect(fmtRunDuration(1500)).toBe("1.5 s");
    expect(fmtRunDuration(900)).toBe("900 ms");
  });

  it("em dash while the run has no duration yet (running)", () => {
    expect(fmtRunDuration(null)).toBe("—");
  });
});

describe("fmtRunTokens", () => {
  it("localizes non-zero counts", () => {
    expect(fmtRunTokens(1234)).toBe((1234).toLocaleString());
  });

  it("em dash for 0 — a running row shows live status WITHOUT tokens (B2)", () => {
    // Las columnas denormalizadas se persisten en finalize: durante el run son
    // 0 y la lista NO debe enseñar un falso «0 tokens».
    expect(fmtRunTokens(0)).toBe("—");
  });
});

describe("fmtRunWhen", () => {
  it("falls back to the raw string on an unparseable date", () => {
    expect(fmtRunWhen("garbage")).toBe("garbage");
  });
});

// --- E1 (runs-visor): etiquetas de estado en los dos idiomas soportados ------

describe("runStatusLabel i18n (E1)", () => {
  it("defaults to Spanish and supports English", () => {
    expect(runStatusLabel("running")).toBe("En curso");
    expect(runStatusLabel("running", "en")).toBe("Running");
    expect(runStatusLabel("needs_human_review", "en")).toBe("Human review");
  });

  it("falls back to the raw status in both languages", () => {
    expect(runStatusLabel("weird", "es")).toBe("weird");
    expect(runStatusLabel("weird", "en")).toBe("weird");
  });
});
