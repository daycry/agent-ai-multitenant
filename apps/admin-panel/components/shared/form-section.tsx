import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * FormSection — a labelled wrapper for a group of related fields inside
 * a longer form. Gives every section the same title / description /
 * spacing rhythm so multi-section settings & edit pages stop hand-rolling
 * `<h*>` + helper-text markup.
 *
 *   <FormSection title={t("identity")} description={t("identityHelp")}>
 *     <Field label={t("name")}>…</Field>
 *     <Field label={t("description")}>…</Field>
 *   </FormSection>
 *
 * The example goes through `useT()` on purpose: `check-i18n.mjs` does not tell
 * comments from code, so a JSDoc with Spanish literals is counted as UI debt.
 * This file was carrying **4 of the guard's pending attributes** without a
 * single translatable string in its code.
 *
 * Presentation only — it renders a plain `<section>` and forwards
 * `className` + `data-testid`. The title is associated to the region via
 * `aria-labelledby` for screen readers.
 */
interface FormSectionProps extends Omit<React.HTMLAttributes<HTMLElement>, "title"> {
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Optional node aligned to the right of the title (e.g. a toggle). */
  action?: React.ReactNode;
  /** Spacing applied between the section's children. Defaults to gap-4. */
  contentClassName?: string;
}

let sectionSeq = 0;

export const FormSection = React.forwardRef<HTMLElement, FormSectionProps>(
  ({ title, description, action, className, contentClassName, children, ...props }, ref) => {
    // Stable id so the heading can label the region for a11y.
    const headingId = React.useMemo(() => `form-section-${(sectionSeq += 1)}`, []);
    const labelled = title !== undefined;

    return (
      <section
        ref={ref}
        className={cn("space-y-4", className)}
        aria-labelledby={labelled ? headingId : undefined}
        {...props}
      >
        {(title !== undefined || description !== undefined || action) && (
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              {title !== undefined && (
                <h3
                  id={headingId}
                  className="text-foreground text-sm font-semibold leading-none tracking-tight"
                >
                  {title}
                </h3>
              )}
              {description !== undefined && (
                <p className="text-muted-foreground text-sm">{description}</p>
              )}
            </div>
            {action ? <div className="shrink-0">{action}</div> : null}
          </div>
        )}
        <div className={cn("flex flex-col gap-4", contentClassName)}>{children}</div>
      </section>
    );
  },
);
FormSection.displayName = "FormSection";
