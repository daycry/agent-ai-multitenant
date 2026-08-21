/**
 * `GET /marketplace/installations/{id}/update-check`: su forma y las preguntas
 * que se le hacen — compartido por la FICHA y por el CATÁLOGO (`task_mkt2_12`).
 *
 * ## Por qué este fichero existe
 *
 * El banner de la ficha (`installations/[id]/update-banner.tsx`) nació con
 * estos tipos y predicados dentro. Cuando el catálogo tuvo que enterarse de lo
 * mismo —«tienes instalaciones que se han quedado atrás»— había dos caminos:
 * copiar las cuatro funciones o sacarlas aquí. Copiarlas es cómo dos pantallas
 * acaban discrepando sobre si una actualización «pide más permisos», que es
 * justo el dato del que depende si hace falta re-consentir.
 *
 * Sin JSX ni hooks a propósito: lo que se puede probar sin montar un árbol de
 * componentes vive aquí, igual que `deployment-types.ts`.
 *
 * ## La trampa de `update_available`
 *
 * `update_available` NO significa «hay algo más nuevo». En el backend es
 * literalmente `target_version is not None` (ver `versioning.py::
 * UpdateAssessment`), o sea «hay un destino al que puedo saltar YA». Cuando lo
 * único más nuevo cruza un MAJOR y nadie ha pedido el opt-in, el backend
 * responde `update_available=false` + `outdated=true` + `target_version=null`.
 *
 * Quien gatea la UI por `update_available` deja el salto de major INVISIBLE: ni
 * se anuncia ni se ofrece el opt-in, y el administrador nunca se entera de que
 * existe una versión mayor. De ahí :func:`hasUpdate`, que es la pregunta que la
 * UI quiere hacer de verdad — «¿hay algo más nuevo, sea del tipo que sea?» — y
 * que las dos superficies comparten.
 */

/** Un delta de permisos entre la versión pinada y la candidata. */
export interface PermissionDelta {
  added: { type: string; value?: unknown }[];
  removed: { type: string; value?: unknown }[];
  changed: { type: string; from: unknown; to: unknown }[];
}

/** La respuesta de `update-check` (espeja `InstallationUpdateCheckResponse`). */
export interface UpdateCheck {
  installation_id: string;
  /** El listing del que cuelga: es la unión con el catálogo, que va por listing. */
  listing_id: string;
  /** El nombre de la capacidad, para no tener que cruzarlo con el catálogo. */
  name: string;
  installed_version: string;
  latest_version: string;
  target_version: string | null;
  outdated: boolean;
  update_available: boolean;
  latest_is_major_bump: boolean;
  permission_delta: PermissionDelta | null;
  requires_consent: boolean;
}

/** La ruta de la comprobación, con su opt-in de major explícito. */
export function updateCheckPath(installationId: string, allowMajor: boolean): string {
  return `/marketplace/installations/${installationId}/update-check?allow_major=${allowMajor}`;
}

/**
 * La clave de caché de react-query, ÚNICA para las dos superficies.
 *
 * El catálogo pregunta por todas las instalaciones con `allowMajor=false` y la
 * ficha por la suya; compartir la clave hace que abrir la ficha desde el aviso
 * del catálogo no vuelva a pedir lo que ya se pidió.
 */
export function updateCheckKey(installationId: string, allowMajor: boolean): unknown[] {
  return ["marketplace-update-check", installationId, allowMajor];
}

/**
 * ¿Hay algo más nuevo que lo instalado, cruce o no cruce un major?
 *
 * Ver la nota de cabecera: `update_available` solo cubre el caso en que YA hay
 * destino elegible. Los dos juntos son la pregunta completa.
 */
export function hasUpdate(check: UpdateCheck | undefined | null): boolean {
  if (!check) return false;
  return check.outdated || check.update_available;
}

/**
 * ¿Este salto está esperando el opt-in de MAJOR?
 *
 * `latest_is_major_bump` con `target_version` a null es el backend diciendo
 * «hay una versión mayor y no te propongo saltar a ella sin que lo pidas».
 */
export function awaitsMajorOptIn(check: UpdateCheck): boolean {
  return check.latest_is_major_bump && check.target_version === null;
}

/** La versión de la que habla el aviso: la propuesta, o la más alta si no hay. */
export function proposedVersion(check: UpdateCheck): string {
  return check.target_version ?? check.latest_version;
}

/** ¿Este delta amplía algo? Quitar un permiso no pide decisión: no amplía nada. */
export function deltaWidens(delta: PermissionDelta | null | undefined): boolean {
  if (!delta) return false;
  return delta.added.length > 0 || delta.changed.length > 0;
}

/**
 * Los tipos de permiso sobre los que el update va a preguntar.
 *
 * Espeja `marketplace/update_consent.py::pending_consent_types`. Se duplica en
 * el cliente por lo mismo que el diff de la cola de revisión: pintarlo exige
 * tenerlo antes de llamar, y pedir un endpoint por fila sería una ida y vuelta
 * por instalación.
 */
export function pendingTypes(delta: PermissionDelta | null | undefined): string[] {
  if (!delta) return [];
  return [
    ...new Set([...delta.added.map((p) => p.type), ...delta.changed.map((c) => c.type)]),
  ].sort();
}

/**
 * ¿Aplicar esto va a exigir consentir permisos?
 *
 * Dos fuentes que dicen lo mismo desde sitios distintos: la bandera que calcula
 * el backend y los tipos que se deducen del propio delta. Se aceptan las dos
 * porque una instalación sin histórico llega sin delta pero con la bandera, y
 * un delta que ensancha un permiso ya concedido llega sin bandera.
 */
export function requiresConsent(check: UpdateCheck): boolean {
  return check.requires_consent || pendingTypes(check.permission_delta).length > 0;
}
