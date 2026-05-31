"use client";

/**
 * Error boundary for the admin shell (frontend-admin-panel-8).
 *
 * Before Plan 06.14 a render error anywhere under `/admin/*` propagated
 * to the React root and unmounted the whole tree, leaving the user with
 * a blank white screen and no way back other than a manual browser
 * reload. React only surfaces render-time errors to the nearest *class*
 * component that implements `componentDidCatch` / `getDerivedStateFrom
 * Error`, so this has to be a class — hooks cannot catch render errors.
 *
 * Recovery model:
 *   - `getDerivedStateFromError` flips to a fallback that keeps the app
 *     mounted (no white screen).
 *   - "Reintentar" calls `reset()` which clears local state AND, via the
 *     `onReset` prop wired to TanStack's `QueryErrorResetBoundary`,
 *     resets any errored queries so a re-render actually re-fetches
 *     instead of immediately re-throwing the same cached error.
 *   - "Recargar la página" is the hard escape hatch (`location.reload`)
 *     for errors that survive a soft reset (e.g. corrupted module state).
 *
 * Kept dependency-free of the language context on purpose: this boundary
 * wraps the providers, so it renders even if `LanguageProvider` itself
 * is what threw. Text is Spanish-primary with an English subtitle, in
 * line with CLAUDE.md §12.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

interface AdminErrorBoundaryProps {
  children: ReactNode;
  /**
   * Invoked when the user asks to recover. Wired to
   * `QueryErrorResetBoundary.reset` so errored queries are retried on the
   * next render rather than replaying their cached rejection.
   */
  onReset?: () => void;
}

interface AdminErrorBoundaryState {
  error: Error | null;
}

export class AdminErrorBoundary extends Component<
  AdminErrorBoundaryProps,
  AdminErrorBoundaryState
> {
  override state: AdminErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): AdminErrorBoundaryState {
    return { error };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surface the failure in the dev console with the component stack so
    // a developer can trace which subtree threw. In production this is
    // where a client-side error reporter would hook in (out of scope for
    // Plan 06.14 — no telemetry endpoint exists for the panel yet).
    // eslint-disable-next-line no-console
    console.error("[admin] render error caught by boundary", error, info.componentStack);
  }

  private reset = (): void => {
    this.props.onReset?.();
    this.setState({ error: null });
  };

  private reload = (): void => {
    if (typeof window !== "undefined") {
      window.location.reload();
    }
  };

  override render(): ReactNode {
    const { error } = this.state;
    if (!error) {
      return this.props.children;
    }

    return (
      <main
        role="alert"
        data-testid="admin-error-boundary"
        className="bg-background flex min-h-screen items-center justify-center p-6"
      >
        <div className="bg-card text-card-foreground border-border w-full max-w-md rounded-lg border p-8 text-center shadow-sm">
          <h1 className="text-foreground text-lg font-semibold">Algo ha fallado</h1>
          <p className="text-muted-foreground mt-1 text-sm">Something went wrong</p>
          <p className="text-muted-foreground mt-4 text-sm">
            Se ha producido un error inesperado al renderizar el panel. Puedes reintentar la última
            acción o recargar la página.
          </p>
          <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-center">
            <button
              type="button"
              onClick={this.reset}
              data-testid="admin-error-retry"
              className="bg-primary text-primary-foreground hover:bg-primary/90 focus-visible:ring-ring inline-flex h-10 items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
            >
              Reintentar
            </button>
            <button
              type="button"
              onClick={this.reload}
              data-testid="admin-error-reload"
              className="border-input bg-background hover:bg-muted hover:text-foreground focus-visible:ring-ring inline-flex h-10 items-center justify-center rounded-md border px-4 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
            >
              Recargar la página
            </button>
          </div>
        </div>
      </main>
    );
  }
}
