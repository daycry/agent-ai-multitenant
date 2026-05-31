"use client";

import { ArrowLeft, ArrowRight } from "lucide-react";
import { useCallback, useState } from "react";

import { useWizard } from "@/lib/use-wizard";
import { cn } from "@/lib/utils";
import { stepById, stepIndex, WIZARD_STEPS } from "@/lib/wizard";

import { StepPanel } from "./step-panel";
import { Stepper } from "./stepper";

/**
 * The wizard shell: a left-hand stepper + the current step's panel + the
 * forward/back navigation. Phase A wires the navigation against the pure
 * client state machine (`useWizard`); the per-step content is filled by tasks
 * 15_02–15_06.
 */
export function WizardShell() {
  const wizard = useWizard();
  const meta = stepById(wizard.current);
  const total = WIZARD_STEPS.length;
  const position = stepIndex(wizard.current) + 1;

  // Step 1 (resources) gate: "next" is blocked until the prerequisite check
  // reports no required failures. Other steps are not gated here (their own
  // validation arrives in later tasks). Starts closed and opens once the
  // prereq panel reports it can proceed.
  const [prereqGateOpen, setPrereqGateOpen] = useState(false);
  const onGateChange = useCallback((canProceed: boolean) => {
    setPrereqGateOpen(canProceed);
  }, []);

  const blockedByPrereqs = wizard.current === "resources" && !prereqGateOpen;
  const canAdvance = wizard.canAdvance && !blockedByPrereqs;

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-5xl flex-col gap-6 px-6 py-10">
      <header className="flex flex-col gap-1">
        <p className="text-muted-foreground text-sm">agentic-platform · instalador</p>
        <h1 className="text-3xl font-semibold tracking-tight" data-testid="wizard-title">
          Instalación
        </h1>
        <p className="text-muted-foreground text-sm" data-testid="wizard-progress">
          Paso {position} de {total}: {meta.titleEs}
        </p>
      </header>

      <div className="grid flex-1 gap-6 md:grid-cols-[16rem_1fr]">
        <aside className="rounded-lg border border-border bg-card p-3">
          <Stepper current={wizard.current} furthest={wizard.furthest} onSelect={wizard.goTo} />
        </aside>

        <div className="flex flex-col rounded-lg border border-border bg-card p-6">
          <div className="flex-1" data-testid="wizard-panel">
            <StepPanel step={wizard.current} onGateChange={onGateChange} />
          </div>

          <footer className="mt-8 flex items-center justify-between border-t border-border pt-4">
            <button
              type="button"
              data-testid="wizard-back"
              disabled={!wizard.canGoBack}
              onClick={wizard.goBack}
              className={cn(
                "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm transition-colors",
                "text-muted-foreground hover:bg-muted",
                !wizard.canGoBack && "cursor-not-allowed opacity-40 hover:bg-transparent",
              )}
            >
              <ArrowLeft className="h-4 w-4" />
              Atrás
            </button>

            <button
              type="button"
              data-testid="wizard-next"
              disabled={!canAdvance}
              onClick={wizard.advance}
              className={cn(
                "inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity",
                !canAdvance && "cursor-not-allowed opacity-40",
              )}
            >
              {meta.isConfirmation ? "Instalar" : "Siguiente"}
              <ArrowRight className="h-4 w-4" />
            </button>
          </footer>
        </div>
      </div>
    </main>
  );
}
