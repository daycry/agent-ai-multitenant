"use client";

import { AlertTriangle, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { INSTALLER_API_BASE } from "@/lib/prereqs";

/**
 * El aviso que faltaba: este wizard no instala nada, y lo dice en pantalla.
 *
 * ## Por qué existe este fichero
 *
 * Hasta el 2026-08-28 un operador podía levantar el contenedor del instalador,
 * abrir `http://host:3100`, recorrer nueve pasos —prerequisitos en verde, barra
 * de progreso, log paso a paso—, leer «Instalación completada. La plataforma
 * está instalada» y apuntar en su gestor de contraseñas un usuario admin, una
 * contraseña, un root token de Vault y cinco unseal keys, bajo el aviso de que
 * se muestran una sola vez y no hay forma de recuperarlas.
 *
 * No había nada instalado. El ejecutor por defecto del backend es un
 * `FakeStepExecutor` y esas credenciales las fabrica `secrets.token_urlsafe`.
 * Todo eso estaba escrito —en un docstring de Python, en un comentario de YAML,
 * en dos README y en un runbook— y `grep -i 'simulaci|simulation|fake'` sobre
 * `app/` y `lib/` daba **cero** resultados. La honestidad vivía en los cuatro
 * sitios que ese operador no abrió.
 *
 * ## Cómo se entera la UI
 *
 * `GET /api/mode` responde `simulated` (los seams cableados son fakes) e
 * `install_enabled` (hay autorización para correr la simulación). Se calculan
 * mirando los seams REALES, no una constante: el día que alguien conecte el
 * ejecutor de verdad, `simulated` pasa a `false` y estos avisos desaparecen
 * solos, sin que nadie tenga que acordarse de borrarlos.
 *
 * **Si la ruta no responde, se asume simulación.** Equivocarse avisando de más
 * deja a un operador molesto; equivocarse avisando de menos le deja apuntando
 * unas unseal keys que no abren nada. La asimetría no está repartida, así que
 * el fallback tampoco.
 */

/** Lo que `/api/mode` responde (espejo de `InstallerModeResponse`). */
export interface InstallerMode {
  readonly simulated: boolean;
  readonly allow_simulation: boolean;
  /** ¿Responden `/api/install/stream` y `/api/finalize/reveal`, o dan 501? */
  readonly install_enabled: boolean;
  readonly real_path: string;
  readonly notice_es: string;
  readonly notice_en: string;
}

/**
 * El modo que se asume mientras `/api/mode` no ha contestado, y el que queda si
 * no contesta nunca. Es el conservador: avisa y no deja instalar.
 */
export const ASSUMED_SIMULATION: InstallerMode = {
  simulated: true,
  allow_simulation: false,
  install_enabled: false,
  real_path: "./scripts/install.sh --config install.yaml",
  notice_es:
    "SIMULACIÓN: este wizard NO instala nada. No se ha arrancado ningún stack, no se ha " +
    "inicializado Vault y no existe ningún usuario administrador. Las credenciales que muestre " +
    "no abren nada.",
  notice_en:
    "SIMULATION: this wizard installs NOTHING. No stack was started, no Vault was initialised " +
    "and no admin user exists. Any credentials it shows open nothing.",
};

/** Pide el modo al backend una vez. Ante cualquier fallo, asume simulación. */
export function useInstallerMode(): InstallerMode {
  const [mode, setMode] = useState<InstallerMode>(ASSUMED_SIMULATION);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(`${INSTALLER_API_BASE}/api/mode`, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    })
      .then(async (resp) => {
        if (!resp.ok) {
          throw new Error(`mode failed: HTTP ${resp.status}`);
        }
        return (await resp.json()) as InstallerMode;
      })
      .then((value) => {
        if (!controller.signal.aborted) {
          setMode(value);
        }
      })
      .catch(() => {
        // Silencio deliberado: el estado por defecto YA es el conservador, y un
        // error de red no puede convertir «no lo sé» en «es una instalación
        // real». No se registra el error para no dar la impresión de que hay
        // algo que arreglar cuando el backend simplemente no está.
      });
    return () => controller.abort();
  }, []);

  return mode;
}

/**
 * La banda permanente. Va en el shell, NO dentro de un paso: el operador que
 * entra directo al paso 8 desde un enlace tiene que verla igual.
 */
export function SimulationBanner({ mode }: { mode: InstallerMode }) {
  if (!mode.simulated) {
    return null;
  }
  return (
    <div
      data-testid="simulation-banner"
      role="alert"
      className="flex items-start gap-3 rounded-lg border-2 border-red-600 bg-red-600/15 px-4 py-3"
    >
      <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-red-600" />
      <div className="flex flex-col gap-1 text-sm">
        <p className="font-semibold uppercase tracking-wide text-red-600">
          Simulación — este asistente no instala nada
        </p>
        <p className="text-red-700 dark:text-red-300">{mode.notice_es}</p>
        <p className="text-red-700/80 dark:text-red-300/80">
          Camino real: <code className="font-mono">{mode.real_path}</code>
        </p>
      </div>
    </div>
  );
}

/**
 * El diálogo bloqueante que se interpone entre el operador y el botón
 * «Instalar». No es un `confirm()` de cortesía: hay que marcar la casilla que
 * dice, con esas palabras, que no se va a instalar nada. Un aviso que se cierra
 * con Enter no es un aviso, es un trámite.
 */
export function SimulationGateDialog({
  mode,
  onCancel,
  onAccept,
}: {
  mode: InstallerMode;
  onCancel: () => void;
  /** `undefined` cuando la simulación no está autorizada: no hay salida hacia delante. */
  onAccept?: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      data-testid="simulation-gate"
      role="dialog"
      aria-modal="true"
      aria-labelledby="simulation-gate-title"
    >
      <div className="border-border bg-card flex max-w-xl flex-col gap-4 rounded-lg border-2 border-red-600 p-6 shadow-xl">
        <h2
          id="simulation-gate-title"
          className="flex items-center gap-2 text-xl font-semibold tracking-tight text-red-600"
        >
          <AlertTriangle className="h-5 w-5 shrink-0" />
          Esto es una simulación
        </h2>

        <p className="text-sm">{mode.notice_es}</p>

        <p className="text-muted-foreground text-sm">
          Los pasos que has rellenado (dominio, recursos, almacenamiento, proveedores y tenant) sí
          se han validado de verdad contra el backend. Lo que no existe es el aprovisionamiento: el
          progreso que verás a continuación está guionizado y las credenciales del último paso son
          valores desechables que no abren nada.
        </p>

        <p className="text-sm">
          Para instalar de verdad: <code className="font-mono text-xs">{mode.real_path}</code>
        </p>

        {onAccept === undefined ? (
          <p
            data-testid="simulation-gate-disabled"
            className="rounded-md border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-600"
          >
            La simulación no está autorizada en este backend, así que el botón «Instalar» no lleva a
            ninguna parte: <code className="font-mono text-xs">/api/install/stream</code> responde
            501. Si sólo quieres revisar el flujo de pantallas, arranca el instalador con{" "}
            <code className="font-mono text-xs">INSTALLER_ALLOW_SIMULATION=1</code>.
          </p>
        ) : (
          <label className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              data-testid="simulation-gate-ack"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              className="mt-1 h-4 w-4"
            />
            <span>
              Entiendo que <strong>no se va a instalar nada</strong> y que las credenciales que se
              muestren al final no sirven para entrar en ningún sitio.
            </span>
          </label>
        )}

        <div className="flex items-center justify-end gap-3">
          <button
            type="button"
            data-testid="simulation-gate-cancel"
            onClick={onCancel}
            className="text-muted-foreground hover:bg-muted rounded-md px-4 py-2 text-sm transition-colors"
          >
            Volver
          </button>
          {onAccept !== undefined && (
            <button
              type="button"
              data-testid="simulation-gate-continue"
              disabled={!acknowledged}
              onClick={onAccept}
              className="inline-flex items-center gap-2 rounded-md border-2 border-red-600 px-4 py-2 text-sm font-medium text-red-600 transition-opacity disabled:cursor-not-allowed disabled:opacity-40"
            >
              Continuar con la simulación
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
