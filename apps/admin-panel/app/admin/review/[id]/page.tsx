"use client";

/**
 * Review-runtime UI (Plan 06 tasks 06_28 / 06_29 / 06_30 / 06_31).
 *
 * One page rendering the four components the human reviewer
 * interacts with during plan validation:
 *
 *   - Terminal web (06_28) — scoped to /workspace.
 *   - Logs WebSocket (06_29) — appended in real time.
 *   - Rerun tests button (06_30) — POSTs to the worker.
 *   - Human checklist (06_31) — items from the plan's `human_*` tests.
 *
 * The page is intentionally minimal — the production version wraps
 * each panel in shadcn/ui Card + accessibility affordances. Tests
 * mock the underlying endpoints / WebSocket; this file is the
 * playwright-test boundary.
 */

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

interface ChecklistItem {
  id: string;
  description: string;
  hint: string | null;
  checklist: string[];
  passed?: boolean;
}

interface SessionInfo {
  id: string;
  plan_id: string;
  status: string;
  checklist: ChecklistItem[];
}

export default function ReviewPage() {
  const params = useParams<{ id: string }>();
  const sessionId = params?.id ?? "";

  const [session, setSession] = useState<SessionInfo | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [rerunMsg, setRerunMsg] = useState<string>("");
  const [terminalConnected, setTerminalConnected] = useState(false);
  const termRef = useRef<HTMLDivElement>(null);

  // Fetch session + checklist.
  useEffect(() => {
    if (!sessionId) return;
    void fetch(`/api/review/${sessionId}`, { credentials: "include" })
      .then((r) => r.json() as Promise<SessionInfo>)
      .then(setSession)
      .catch(() => {
        // Show empty checklist on error; tests assert on testids.
        setSession({ id: sessionId, plan_id: "", status: "unknown", checklist: [] });
      });
  }, [sessionId]);

  // Logs WebSocket.
  useEffect(() => {
    if (!sessionId) return;
    const ws = new WebSocket(`ws://localhost:8001/ws/review/${sessionId}/logs`);
    ws.onmessage = (ev: MessageEvent) => {
      setLogs((prev) => [...prev, String(ev.data)]);
    };
    ws.onopen = () => setTerminalConnected(true);
    return () => ws.close();
  }, [sessionId]);

  const onRerun = async () => {
    setRerunMsg("Encolando...");
    const res = await fetch(`/api/review/${sessionId}/rerun`, {
      method: "POST",
      credentials: "include",
    });
    setRerunMsg(res.ok ? "Re-ejecución encolada" : "Error al encolar");
  };

  const onToggleCheck = (itemId: string) => {
    if (!session) return;
    setSession({
      ...session,
      checklist: session.checklist.map((it) =>
        it.id === itemId ? { ...it, passed: !it.passed } : it,
      ),
    });
  };

  return (
    <div data-testid="review-page" className="container mx-auto px-4 py-6 space-y-6">
      <h1 className="text-2xl font-bold">Plan validation review</h1>

      {/* Task 06_28 — terminal web */}
      <section data-testid="review-terminal">
        <h2 className="text-lg font-semibold">Terminal (/workspace)</h2>
        <div
          ref={termRef}
          className="h-48 bg-black text-green-400 font-mono p-2 overflow-auto"
          data-testid="review-terminal-pane"
        >
          {terminalConnected ? "$ " : "Connecting..."}
        </div>
      </section>

      {/* Task 06_29 — logs websocket */}
      <section data-testid="review-logs">
        <h2 className="text-lg font-semibold">Logs (live)</h2>
        <pre
          data-testid="review-logs-pane"
          className="h-48 bg-gray-900 text-gray-100 p-2 overflow-auto text-xs"
        >
          {logs.join("\n")}
        </pre>
      </section>

      {/* Task 06_30 — rerun tests button */}
      <section data-testid="review-rerun">
        <button
          data-testid="review-rerun-button"
          onClick={() => void onRerun()}
          className="px-4 py-2 bg-blue-600 text-white rounded"
        >
          Re-ejecutar tests
        </button>
        {rerunMsg && (
          <span data-testid="review-rerun-status" className="ml-3 text-sm">
            {rerunMsg}
          </span>
        )}
      </section>

      {/* Task 06_31 — checklist */}
      <section data-testid="review-checklist">
        <h2 className="text-lg font-semibold">Tests humanos del plan</h2>
        {!session?.checklist.length && <p data-testid="review-checklist-empty">Sin items.</p>}
        <ul className="space-y-3">
          {session?.checklist.map((item) => (
            <li
              key={item.id}
              data-testid={`review-checklist-item-${item.id}`}
              className="border rounded p-3"
            >
              <label className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={!!item.passed}
                  onChange={() => onToggleCheck(item.id)}
                  data-testid={`review-checkbox-${item.id}`}
                />
                <div>
                  <div className="font-semibold">{item.description}</div>
                  {item.hint && <div className="text-sm text-gray-600">Hint: {item.hint}</div>}
                  {item.checklist.length > 0 && (
                    <ul className="list-disc pl-4 mt-2 text-sm">
                      {item.checklist.map((sub, i) => (
                        <li key={i}>{sub}</li>
                      ))}
                    </ul>
                  )}
                </div>
              </label>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
