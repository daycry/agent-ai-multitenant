// @vitest-environment jsdom
// Replay de runs (ADR 0119): barra de reproducción del steps_log. Componente
// PURO (recibe total y estado, emite cambios): el timing de auto-avance se
// verifica con timers falsos; la semántica de cada step la pone
// lib/office/mapping en la ficha, no aquí.

import { cleanup, fireEvent, render, screen, act } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ReplayBar } from "@/components/executions/replay-bar";

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("ReplayBar", () => {
  it("play avanza el índice al ritmo elegido y pausa lo congela", () => {
    vi.useFakeTimers();
    const onIndexChange = vi.fn();
    render(<ReplayBar total={5} index={0} onIndexChange={onIndexChange} />);

    fireEvent.click(screen.getByTestId("replay-play"));
    act(() => {
      vi.advanceTimersByTime(900); // velocidad por defecto: 1 step / 800ms
    });
    expect(onIndexChange).toHaveBeenCalledWith(1);

    fireEvent.click(screen.getByTestId("replay-play")); // pausa
    onIndexChange.mockClear();
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(onIndexChange).not.toHaveBeenCalled();
  });

  it("el scrubber salta a cualquier paso y al llegar al final se detiene", () => {
    vi.useFakeTimers();
    const onIndexChange = vi.fn();
    render(<ReplayBar total={3} index={2} onIndexChange={onIndexChange} />);

    fireEvent.change(screen.getByTestId("replay-scrubber"), { target: { value: "1" } });
    expect(onIndexChange).toHaveBeenCalledWith(1);

    // En el último paso, play no avanza más allá del final.
    fireEvent.click(screen.getByTestId("replay-play"));
    onIndexChange.mockClear();
    act(() => {
      vi.advanceTimersByTime(2000);
    });
    for (const call of onIndexChange.mock.calls) {
      expect(call[0]).toBeLessThanOrEqual(2);
    }
  });
});
