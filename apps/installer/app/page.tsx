import { WizardShell } from "./wizard-shell";

/**
 * Installer entry point. Renders the 9-step wizard shell (task_15_01). The
 * shell handles navigation against the client-side state machine; per-step
 * content is filled by tasks 15_02–15_06.
 */
export default function InstallerPage() {
  return <WizardShell />;
}
