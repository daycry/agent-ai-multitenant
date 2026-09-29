import type { Page } from "@playwright/test";

/**
 * Espera a que React haya hidratado el shell de `/admin/*`.
 *
 * Con `waitUntil: "domcontentloaded"` el HTML ya está pintado pero los
 * `onClick` aún no existen: un clic en ese hueco se pierde sin error. El shell
 * pone `data-hydrated="true"` en `<html>` desde un `useEffect`
 * (`components/layout/hydration-marker.tsx`), que sólo corre tras hidratar.
 * Llámala tras `page.goto` y antes del primer clic.
 */
export async function waitForHydration(page: Page): Promise<void> {
  await page.locator("html[data-hydrated='true']").waitFor({ state: "attached" });
}
