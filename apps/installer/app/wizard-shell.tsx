"use client";

import { ArrowLeft, ArrowRight } from "lucide-react";
import { useCallback, useState } from "react";

import { isConfigStep, validateStep } from "@/lib/config";
import { useConfig } from "@/lib/use-config";
import { useWizard } from "@/lib/use-wizard";
import { cn } from "@/lib/utils";
import { stepById, stepIndex, WIZARD_STEPS, type WizardStepId } from "@/lib/wizard";

import { StepPanel, stepHasBlockingErrors } from "./step-panel";
import { Stepper } from "./stepper";

/**
 * The wizard shell: a left-hand stepper + the current step's panel + the
 * forward/back navigation. Holds the captured config (steps 2-6, task_15_03) in
 * client state and gates "next" on client-side validation per step. The prereq
 * step (resources) additionally gates on the backend probe (task_15_02).
 */
export function WizardShell() {
  const wizard = useWizard();
  const config = useConfig();
  const meta = stepById(wizard.current);
  const total = WIZARD_STEPS.length;
  const position = stepIndex(wizard.current) + 1;

  // The resources step gates "next" on the prereq probe (no required failures).
  const [prereqGateOpen, setPrereqGateOpen] = useState(false);
  const onGateChange = useCallback((canProceed: boolean) => {
    setPrereqGateOpen(canProceed);
  }, []);

  // Inline errors appear only after the user attempts to advance a config step.
  const [showErrorsFor, setShowErrorsFor] = useState<ReadonlySet<WizardStepId>>(new Set());

  // Summary step (task_15_04): the operator must tick the confirm gate before
  // the irreversible install can start. Reset whenever we navigate away so a
  // change of config forces re-confirmation.
  const [confirmed, setConfirmed] = useState(false);
  const onConfirmChange = useCallback((value: boolean) => {
    setConfirmed(value);
  }, []);

  // Install step (task_15_05): "next" (to the done step) is gated on the
  // install run reaching its terminal success state.
  const [installComplete, setInstallComplete] = useState(false);
  const onInstallComplete = useCallback(() => {
    setInstallComplete(true);
  }, []);

  const blockedByPrereqs = wizard.current === "resources" && !prereqGateOpen;
  const blockedByConfirm = wizard.current === "summary" && !confirmed;
  const blockedByInstall = wizard.current === "install" && !installComplete;
  const blockedByConfig =
    isConfigStep(wizard.current) && stepHasBlockingErrors(wizard.current, config.config);

  const handleNext = useCallback(() => {
    const step = wizard.current;
    if (isConfigStep(step)) {
      // Re-validate; if invalid, surface errors for this step and don't advance.
      const errors = validateStep(step, config.config);
      if (Object.keys(errors).length > 0) {
        setShowErrorsFor((prev) => new Set(prev).add(step));
        return;
      }
    }
    if (step === "resources" && !prereqGateOpen) {
      return;
    }
    if (step === "summary" && !confirmed) {
      return;
    }
    if (step === "install" && !installComplete) {
      return;
    }
    wizard.advance();
  }, [wizard, config.config, prereqGateOpen, confirmed, installComplete]);

  // Going back from the summary step un-confirms (the operator may edit config).
  const handleBack = useCallback(() => {
    if (wizard.current === "summary") {
      setConfirmed(false);
    }
    wizard.goBack();
  }, [wizard]);

  const handleGoTo = useCallback(
    (id: WizardStepId) => {
      if (wizard.current === "summary" && id !== "summary") {
        setConfirmed(false);
      }
      wizard.goTo(id);
    },
    [wizard],
  );

  // The button stays enabled for config steps (so the click can reveal errors);
  // it's hard-disabled while the prereq probe blocks the resources step or the
  // confirm gate blocks the summary step.
  const nextDisabled =
    !wizard.canAdvance || blockedByPrereqs || blockedByConfirm || blockedByInstall;
  const showErrors = showErrorsFor.has(wizard.current);

  // The install step is irreversible: once provisioning has begun there's no
  // going back to edit config. Disable "back" while on the install step.
  const backDisabled = !wizard.canGoBack || wizard.current === "install";

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
          <Stepper current={wizard.current} furthest={wizard.furthest} onSelect={handleGoTo} />
        </aside>

        <div className="flex flex-col rounded-lg border border-border bg-card p-6">
          <div className="flex-1" data-testid="wizard-panel">
            <StepPanel
              step={wizard.current}
              config={config}
              showErrors={showErrors}
              onGateChange={onGateChange}
              confirmed={confirmed}
              onConfirmChange={onConfirmChange}
              onInstallComplete={onInstallComplete}
            />
          </div>

          <footer className="mt-8 flex items-center justify-between border-t border-border pt-4">
            <button
              type="button"
              data-testid="wizard-back"
              disabled={backDisabled}
              onClick={handleBack}
              className={cn(
                "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm transition-colors",
                "text-muted-foreground hover:bg-muted",
                backDisabled && "cursor-not-allowed opacity-40 hover:bg-transparent",
              )}
            >
              <ArrowLeft className="h-4 w-4" />
              Atrás
            </button>

            <button
              type="button"
              data-testid="wizard-next"
              disabled={nextDisabled}
              data-blocked={blockedByConfig ? "true" : "false"}
              onClick={handleNext}
              className={cn(
                "inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-opacity",
                nextDisabled && "cursor-not-allowed opacity-40",
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
