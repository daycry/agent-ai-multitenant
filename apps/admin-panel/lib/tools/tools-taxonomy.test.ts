import { describe, expect, it } from "vitest";

import {
  CATEGORY,
  IMPL,
  SECURITY,
  humanizeSlug,
  resolveCategory,
  resolveImpl,
  resolveSecurity,
} from "@/lib/tools/taxonomy";

/**
 * Plan 06.18 task_06_18_10 — the shared taxonomy module is the SINGLE source
 * of truth for tool labels/variants across assignment + diagnostic.
 *
 * These tests pin the contract that broke before the refactor:
 *   - the same tool resolves to the SAME label/variant (single source),
 *   - the invented `sensitive` key does NOT exist,
 *   - `sandboxed` → "Aislada",
 *   - the raw enum is NEVER rendered (unknown slugs are humanised, and known
 *     slugs return a proper human label, not the slug itself).
 */

describe("tools taxonomy — single source of truth", () => {
  it("resolves the same security level to identical label/variant on every call", () => {
    // Two independent resolutions (assignment + diagnostic both call this).
    const a = resolveSecurity("privileged", "es");
    const b = resolveSecurity("privileged", "es");
    expect(a.labelEs).toBe(b.labelEs);
    expect(a.labelEn).toBe(b.labelEn);
    expect(a.variant).toBe(b.variant);
    expect(a.variant).toBe("danger");
  });

  it("resolves the same impl type to identical label/variant on every call", () => {
    const a = resolveImpl("docker_command", "es");
    const b = resolveImpl("docker_command", "en");
    // Variant is language-independent and unified (was danger vs info before).
    expect(a.variant).toBe(b.variant);
    expect(a.variant).toBe("info");
    expect(a.labelEs).toBe("Contenedor");
    expect(b.labelEn).toBe("Container");
  });
});

describe("tools taxonomy — closed value-set hygiene (ADR 0049)", () => {
  it('does NOT define the invented "sensitive" security key', () => {
    expect(Object.keys(SECURITY)).not.toContain("sensitive");
    expect(SECURITY).not.toHaveProperty("sensitive");
  });

  it('maps "sandboxed" → "Aislada" (ES)', () => {
    expect(SECURITY.sandboxed.labelEs).toBe("Aislada");
    expect(resolveSecurity("sandboxed", "es").labelEs).toBe("Aislada");
    expect(resolveSecurity("sandboxed", "es").variant).toBe("warning");
  });

  it("mirrors the backend security enum exactly (safe / sandboxed / privileged)", () => {
    expect(Object.keys(SECURITY).sort()).toEqual(["privileged", "safe", "sandboxed"]);
  });

  it("mirrors the backend implementation enum exactly", () => {
    expect(Object.keys(IMPL).sort()).toEqual([
      "builtin",
      "docker_command",
      "http_endpoint",
      "mcp_tool",
      "python_function",
    ]);
  });

  it("mirrors the backend category enum exactly", () => {
    expect(Object.keys(CATEGORY).sort()).toEqual([
      "command",
      "custom",
      "file",
      "git",
      "knowledge",
      "mcp",
      "network",
      "notification",
      "orchestration",
      "runtime",
    ]);
  });
});

describe("tools taxonomy — never render the raw enum", () => {
  it("returns a human label for every known security/impl/category slug (not the slug)", () => {
    for (const slug of Object.keys(SECURITY)) {
      const d = resolveSecurity(slug, "es");
      expect(d.labelEs).not.toBe(slug);
      expect(d.labelEn).not.toBe(slug);
    }
    for (const slug of Object.keys(IMPL)) {
      const d = resolveImpl(slug, "es");
      // Some impl labels are acronyms equal in ES/EN (MCP, HTTP) — those are
      // human labels, not raw enum values like "mcp_tool"/"http_endpoint".
      expect(d.labelEs).not.toBe(slug);
      expect(d.labelEn).not.toBe(slug);
    }
    for (const slug of Object.keys(CATEGORY)) {
      const d = resolveCategory(slug, "es");
      expect(d.labelEs).not.toBe(slug);
      expect(d.labelEn).not.toBe(slug);
    }
  });

  it("humanises an unknown slug instead of leaking it verbatim", () => {
    expect(humanizeSlug("docker_command")).toBe("Docker command");
    const d = resolveImpl("some_future_type", "es");
    expect(d.labelEs).toBe("Some future type");
    expect(d.variant).toBe("muted");
    // The descriptor still keeps the raw value for keying, but never as a label.
    expect(d.value).toBe("some_future_type");
    expect(d.labelEs).not.toBe("some_future_type");
  });

  it("falls back gracefully for an unknown security level (no crash, no raw enum)", () => {
    const d = resolveSecurity("nope", "en");
    expect(d.labelEn).toBe("Nope");
    expect(d.variant).toBe("muted");
  });
});
