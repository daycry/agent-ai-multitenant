"use client";

import { useCallback, useMemo, useState } from "react";

import {
  isFirstStep,
  isLastStep,
  nextStepId,
  previousStepId,
  stepIndex,
  type WizardStepId,
} from "./wizard";

export interface WizardController {
  current: WizardStepId;
  /** Furthest step reached — used to light up visited stepper entries. */
  furthest: WizardStepId;
  canAdvance: boolean;
  canGoBack: boolean;
  advance: () => void;
  goBack: () => void;
  /** Jump to an already-visited step (forward skipping is refused). */
  goTo: (id: WizardStepId) => void;
}

/**
 * Client-side wizard state machine. Pure navigation only — no provisioning
 * happens here (that lives behind the backend's injectable seams). Mirrors the
 * backend's WizardState semantics: you cannot advance past `done`, cannot go
 * back from `welcome`, and cannot jump forward past `furthest`.
 */
export function useWizard(initial: WizardStepId = "welcome"): WizardController {
  const [current, setCurrent] = useState<WizardStepId>(initial);
  const [furthest, setFurthest] = useState<WizardStepId>(initial);

  const advance = useCallback(() => {
    setCurrent((cur) => {
      const next = nextStepId(cur);
      if (next === null) {
        return cur;
      }
      setFurthest((f) => (stepIndex(next) > stepIndex(f) ? next : f));
      return next;
    });
  }, []);

  const goBack = useCallback(() => {
    setCurrent((cur) => previousStepId(cur) ?? cur);
  }, []);

  const goTo = useCallback(
    (id: WizardStepId) => {
      // Refuse forward skips past the furthest visited step.
      if (stepIndex(id) > stepIndex(furthest)) {
        return;
      }
      setCurrent(id);
    },
    [furthest],
  );

  return useMemo(
    () => ({
      current,
      furthest,
      canAdvance: !isLastStep(current),
      canGoBack: !isFirstStep(current),
      advance,
      goBack,
      goTo,
    }),
    [current, furthest, advance, goBack, goTo],
  );
}
