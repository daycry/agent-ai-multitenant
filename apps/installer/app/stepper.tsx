"use client";

import { Check } from "lucide-react";

import { cn } from "@/lib/utils";
import { stepIndex, WIZARD_STEPS, type WizardStepId } from "@/lib/wizard";

interface StepperProps {
  current: WizardStepId;
  furthest: WizardStepId;
  onSelect: (id: WizardStepId) => void;
}

/**
 * The 9-step vertical stepper. Visited steps (index <= furthest) are
 * clickable; future steps are disabled until reached. The current step is
 * highlighted; completed steps show a check.
 */
export function Stepper({ current, furthest, onSelect }: StepperProps) {
  const currentIdx = stepIndex(current);
  const furthestIdx = stepIndex(furthest);

  return (
    <nav aria-label="Pasos de instalación" data-testid="wizard-stepper">
      <ol className="flex flex-col gap-1">
        {WIZARD_STEPS.map((step, idx) => {
          const isCurrent = step.id === current;
          const isVisited = idx <= furthestIdx;
          const isComplete = idx < currentIdx;
          return (
            <li key={step.id}>
              <button
                type="button"
                data-testid={`stepper-item-${step.id}`}
                data-current={isCurrent ? "true" : "false"}
                aria-current={isCurrent ? "step" : undefined}
                disabled={!isVisited}
                onClick={() => onSelect(step.id)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors",
                  isCurrent
                    ? "bg-primary/15 text-foreground"
                    : "text-muted-foreground hover:bg-muted",
                  !isVisited && "cursor-not-allowed opacity-50 hover:bg-transparent",
                )}
              >
                <span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs",
                    isCurrent && "border-primary bg-primary text-primary-foreground",
                    isComplete && "border-success bg-success text-success-foreground",
                    !isCurrent && !isComplete && "border-border",
                  )}
                >
                  {isComplete ? <Check className="h-3.5 w-3.5" /> : idx + 1}
                </span>
                <span className="truncate">{step.titleEs}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
