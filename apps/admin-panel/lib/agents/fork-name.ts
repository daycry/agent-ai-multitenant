/**
 * Nombre sugerido para la copia de un agente (fork).
 *
 * ## Por qué existe
 *
 * La migración 0126 puso dos índices únicos parciales sobre `agents`: un espacio
 * de nombres por (tenant, proyecto) para los agentes de proyecto y otro por
 * tenant para los globales. Es una regla de negocio legítima —el nombre es la
 * identidad con la que se elige un agente en un `role_map` o al montar un
 * equipo—, y su consecuencia es que **forkear dos veces el mismo origen al mismo
 * destino choca**: el backend hereda el nombre del origen cuando no se le da uno
 * y la segunda copia colisiona.
 *
 * El backend responde a esa colisión con **409 y un mensaje accionable**, y
 * deliberadamente NO auto-renombra: renombrar en silencio decide por el usuario
 * algo que después tiene que deshacer. Esta función es la otra mitad de esa
 * decisión: la UI **sugiere** un nombre libre y lo pone en un campo visible y
 * editable, de modo que la sugerencia va DELANTE del usuario en vez de detrás.
 *
 * ## Lo que sabe y lo que no
 *
 * Sólo puede esquivar los nombres que la UI conoce, y lo que la UI conoce es la
 * PRIMERA PÁGINA de `GET /agents` (100 filas). Con un catálogo mayor, una
 * carrera con otro operador o un nombre escrito a mano, el 409 sigue siendo
 * posible — por eso los diálogos ADEMÁS lo tratan. Esto reduce el choque
 * previsible; no lo sustituye, y leerlo como garantía sería el error.
 *
 * Forkear una copia da `«X (copia) (copia)»`. Es feo y es honesto: intentar
 * "desanidar" el sufijo obligaría a parsear la plantilla traducida al revés, y
 * el nombre queda igualmente editable delante del usuario.
 */

import type { Translator } from "@/lib/i18n/translate";

/**
 * Clave de comparación de dos nombres.
 *
 * Se ignoran mayúsculas y espacios de los extremos aunque el índice de Postgres
 * SÍ distinga mayúsculas: `«Ada (copia)»` y `«ada (copia)»` no colisionarían en
 * la BD, pero son indistinguibles para el humano que después tiene que elegir
 * uno de los dos en un `role_map`. Sugerir esa diferencia sería resolver el
 * choque técnico creando uno peor, el de dos agentes que se leen igual.
 */
function key(name: string): string {
  return name.trim().toLowerCase();
}

/**
 * El primer nombre libre para una copia de `sourceName` en el destino.
 *
 * `takenNames` son los nombres YA usados **en el mismo espacio de nombres** que
 * va a recibir la copia (los agentes del proyecto destino, o los globales del
 * tenant): filtrarlos es del llamante, porque sólo él sabe dónde aterriza.
 *
 * Prueba `«X (copia)»` y, si está cogido, `«X (copia 2)»`, `«X (copia 3)»`… El
 * bucle termina siempre sin necesidad de un tope arbitrario: entre el candidato
 * llano y los numerados hasta `takenNames.length + 2` hay más candidatos
 * distintos que nombres cogidos, así que alguno tiene que estar libre.
 */
export function suggestForkName(
  sourceName: string,
  takenNames: Iterable<string>,
  t: Translator<"agents">,
): string {
  const taken = new Set([...takenNames].map(key));
  const plain = t("forkCopySuffix", { name: sourceName });
  if (!taken.has(key(plain))) return plain;

  for (let n = 2; n <= taken.size + 2; n += 1) {
    const candidate = t("forkCopySuffixNumbered", { name: sourceName, n });
    if (!taken.has(key(candidate))) return candidate;
  }

  // Inalcanzable por el argumento de conteo de arriba; si alguien cambia las
  // plantillas para que dejen de depender de `n` (y todos los candidatos
  // numerados pasen a ser el mismo texto), es mejor devolver algo que el
  // usuario pueda editar que lanzar en medio de un render.
  return plain;
}

/**
 * Los nombres ocupados en el proyecto `projectId`.
 *
 * Los dos diálogos que forkean necesitan exactamente esto y el filtro tiene una
 * trampa que conviene escribir una sola vez: el destino se compara con
 * `project_id`, y un agente global (`project_id: null`) NO ocupa nombre en un
 * proyecto — vive en el otro índice, el del tenant.
 */
export function namesTakenInProject(
  agents: readonly { name: string; project_id: string | null }[],
  projectId: string,
): string[] {
  if (!projectId) return [];
  return agents.filter((a) => a.project_id === projectId).map((a) => a.name);
}
