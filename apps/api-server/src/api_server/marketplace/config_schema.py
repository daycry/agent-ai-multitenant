"""Validación de los VALORES de un despliegue contra el `config_schema` (ADR 0142).

La tercera capa del ADR 0142 —el despliegue— captura los valores por proyecto
(`base_url`, timeouts, punteros a credenciales…). Este módulo es quien decide si
esos valores casan con lo que el manifest declaró, y es **puro**: sin BD, sin
red, sin reloj. Dos funciones:

* :func:`validate_deployment_config` — devuelve la lista de errores legibles
  (vacía = válido) y, por separado, los valores **normalizados** con los
  defaults del esquema aplicados. Devolver una lista en vez de levantar es
  deliberado: la UI del formulario guiado quiere pintar TODOS los errores a la
  vez, no el primero.
* :func:`apply_schema_migration` — lo que la fase 4 necesita al actualizar de
  versión: campos nuevos toman su default, los retirados se van, y un requerido
  nuevo **sin** default se señala en vez de aplicarse a medias.

## El dialecto, y por qué no es JSON Schema entero

El `config_schema` que ya existe (`marketplace/playwright.py::config_schema`) es
un documento «JSON-Schema-ish» pensado para que el front-end pinte un formulario:
`type`, `widget`, `enum`, `items.enum`, `minItems`, `minimum`, `default`,
`required`. Aquí se valida **ese** dialecto y solo ése. Meter un validador de
JSON Schema completo sería una dependencia nueva para cubrir construcciones que
ningún manifest usa (y el catálogo es cerrado: nadie publica sin pasar por el
parser).

Lo que sí se valida sin concesiones:

* **tipos** (`string` / `integer` / `number` / `boolean` / `array` / `object`),
  con el detalle de que `bool` NO cuela como entero (en Python es subclase de
  `int`, y aceptarlo convertiría `headless: true` en `timeout_ms: 1`);
* **requeridos** ausentes;
* **campos desconocidos**: rechazados, no ignorados. Un `base_ur1` mal escrito
  que se ignora en silencio produce un despliegue que apunta a otro sitio;
* **`enum`** y, en arrays, `items.enum` + `minItems`;
* **`secret: true` ⇒ puntero a Vault**. El valor tiene que empezar por `vault:`
  (el mismo contrato que `MCPServerConfigModel.auth_ref` ya exige) y **el
  mensaje de error NUNCA ecoa el valor**: un error de validación que imprime el
  secreto lo copia al log, que es donde no debe estar.
"""

from __future__ import annotations

from typing import Any

#: Los tipos del dialecto. Mapeados a los tipos Python que los satisfacen.
_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}

#: Prefijo obligatorio de un puntero a Vault (mismo que `auth_ref`).
VAULT_POINTER_PREFIX = "vault:"

#: Lo que se dice cuando un campo `secret` trae un valor en claro. Constante y
#: sin interpolar el valor: es la mitad del contrato de no filtrar el secreto.
SECRET_MUST_BE_VAULT_POINTER = (
    "debe ser un puntero a Vault (empezar por 'vault:'); "
    "un secreto en claro no se guarda en la configuración del despliegue"
)


def _properties(schema: Any) -> dict[str, dict[str, Any]]:
    """Las `properties` del esquema, saneadas. Esquema no-mapping => vacío."""
    if not isinstance(schema, dict):
        return {}
    raw = schema.get("properties")
    if not isinstance(raw, dict):
        return {}
    return {str(name): dict(spec) for name, spec in raw.items() if isinstance(spec, dict)}


def _required(schema: Any) -> list[str]:
    if not isinstance(schema, dict):
        return []
    raw = schema.get("required")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str)]


def _has_default(spec: dict[str, Any]) -> bool:
    """¿Declara `default`? OJO: `default: None` CUENTA como declarado.

    `playwright.config_schema()` emite `"base_url": {"default": None}` para decir
    «opcional, vacío por defecto». Tratar eso como «sin default» convertiría un
    campo opcional en requerido en cuanto entrase en `required`.
    """
    return "default" in spec


def _is_secret(spec: dict[str, Any]) -> bool:
    return bool(spec.get("secret"))


def _type_error(name: str, expected: str, value: Any) -> str:
    return f"campo {name!r}: se esperaba {expected}, llegó {type(value).__name__}"


def _check_type(name: str, spec: dict[str, Any], value: Any) -> list[str]:
    """Comprueba `type` + `enum` + restricciones de array/número."""
    errors: list[str] = []
    declared = spec.get("type")
    if isinstance(declared, str) and declared in _TYPE_CHECKS:
        allowed = _TYPE_CHECKS[declared]
        # `bool` es subclase de `int`: un booleano NO satisface integer/number.
        if declared in {"integer", "number"} and isinstance(value, bool):
            errors.append(_type_error(name, declared, value))
            return errors
        if not isinstance(value, allowed):
            errors.append(_type_error(name, declared, value))
            return errors

    enum = spec.get("enum")
    if isinstance(enum, list) and enum and value not in enum:
        errors.append(f"campo {name!r}: valor no permitido; admitidos: {', '.join(map(str, enum))}")

    if isinstance(value, list):
        items = spec.get("items")
        if isinstance(items, dict) and isinstance(items.get("enum"), list) and items["enum"]:
            for entry in value:
                if entry not in items["enum"]:
                    errors.append(
                        f"campo {name!r}: entrada no permitida {entry!r}; "
                        f"admitidas: {', '.join(map(str, items['enum']))}"
                    )
        min_items = spec.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            errors.append(f"campo {name!r}: requiere al menos {min_items} elemento(s)")

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = spec.get("minimum")
        if isinstance(minimum, int | float) and value < minimum:
            errors.append(f"campo {name!r}: debe ser >= {minimum}")
        maximum = spec.get("maximum")
        if isinstance(maximum, int | float) and value > maximum:
            errors.append(f"campo {name!r}: debe ser <= {maximum}")

    return errors


def _check_secret(name: str, value: Any) -> list[str]:
    """Un campo `secret: true` solo acepta un puntero a Vault.

    El mensaje no lleva el valor. A propósito y sin excepciones.
    """
    if not isinstance(value, str) or not value.startswith(VAULT_POINTER_PREFIX):
        return [f"campo {name!r}: {SECRET_MUST_BE_VAULT_POINTER}"]
    if value.strip() == VAULT_POINTER_PREFIX:
        return [f"campo {name!r}: el puntero a Vault está vacío"]
    return []


def validate_deployment_config(
    schema: dict[str, Any] | None,
    # `Any` a propósito: esto ES el validador de entrada, así que su trabajo
    # incluye rechazar una lista o un string donde debía venir un objeto.
    # Tipar el parámetro como `dict` convertiría esa guarda en código muerto
    # para mypy y en un `AttributeError` en producción.
    values: Any,
) -> list[str]:
    """Errores legibles de `values` contra `schema`. Lista vacía = válido.

    Args:
        schema: el `config_schema` de la versión desplegada. ``None`` o ``{}``
            significa «esta capacidad no pide configuración»: en ese caso
            cualquier valor suministrado es un error (nadie debería estar
            mandando config a algo que no la declara — un typo del cliente o un
            manifest que cambió sin actualizar el despliegue).
        values: los valores que el formulario del despliegue capturó.

    Returns:
        Lista de mensajes, uno por problema, en orden estable (por nombre de
        campo) para que dos ejecuciones digan lo mismo.
    """
    if values is None:
        values = {}
    if not isinstance(values, dict):
        return ["la configuración del despliegue debe ser un objeto (clave: valor)"]

    props = _properties(schema)
    if not props:
        if values:
            return [
                "esta capacidad no declara `config_schema`, así que no admite "
                f"configuración; llegaron: {', '.join(sorted(map(str, values)))}"
            ]
        return []

    errors: list[str] = []

    unknown = sorted(set(values) - set(props))
    for name in unknown:
        errors.append(
            f"campo desconocido {name!r}: no está en el `config_schema` de la versión desplegada"
        )

    for name in _required(schema):
        spec = props.get(name)
        if name in values and values[name] is not None:
            continue
        # Un requerido con default no es un hueco: el default lo llena.
        if spec is not None and _has_default(spec) and spec.get("default") is not None:
            continue
        errors.append(f"campo requerido ausente: {name!r}")

    for name in sorted(set(values) & set(props)):
        spec = props[name]
        value = values[name]
        if value is None:
            # NULL explícito: válido salvo que sea requerido (ya cazado arriba).
            continue
        if _is_secret(spec):
            errors.extend(_check_secret(name, value))
            continue
        errors.extend(_check_type(name, spec, value))

    return errors


def apply_defaults(
    schema: dict[str, Any] | None,
    values: dict[str, Any] | None,
) -> dict[str, Any]:
    """Los valores con los defaults del esquema aplicados a lo que falte.

    No valida (eso es :func:`validate_deployment_config`); solo rellena. Un
    campo presente —aunque valga ``None``— se respeta: el operador que vacía un
    campo con default no quiere que se le vuelva a llenar solo.
    """
    props = _properties(schema)
    out: dict[str, Any] = dict(values or {})
    for name, spec in props.items():
        if name in out:
            continue
        if _has_default(spec):
            out[name] = spec["default"]
    return out


def apply_schema_migration(
    old_values: dict[str, Any] | None,
    new_schema: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Reencaja la config de un despliegue en el `config_schema` de una versión nueva.

    Es la pieza que la fase 4 (`task_mkt2_12`) usa al re-pinar una instalación:

    * **campo nuevo con default** → toma su default;
    * **campo retirado** → fuera de los valores (no se arrastra basura que la
      versión nueva no entiende);
    * **campo nuevo REQUERIDO y sin default** → NO se inventa nada: se devuelve
      señalado en la lista de problemas para que el llamante deje el despliegue
      `disabled` con motivo en vez de aplicarlo a medias.

    Returns:
        ``(valores_reencajados, problemas)``. Con ``problemas`` no vacío, el
        despliegue NO debe aplicarse: los valores devueltos son lo mejor que se
        pudo reconstruir, útiles para pintar el formulario al humano, no para
        escribir.
    """
    props = _properties(new_schema)
    old = dict(old_values or {})

    if not props:
        # La versión nueva no pide configuración: la vieja se descarta entera.
        return ({}, [])

    migrated: dict[str, Any] = {}
    for name, spec in props.items():
        if name in old:
            migrated[name] = old[name]
        elif _has_default(spec):
            migrated[name] = spec["default"]

    problems: list[str] = []
    for name in _required(new_schema):
        if name not in props:
            problems.append(
                f"el `config_schema` nuevo exige {name!r} pero no lo declara en `properties`"
            )
            continue
        if migrated.get(name) is not None:
            continue
        problems.append(
            f"campo requerido nuevo sin valor ni default: {name!r} — "
            "el despliegue necesita intervención humana antes de aplicarse"
        )

    return (migrated, problems)


def dropped_fields(
    old_values: dict[str, Any] | None,
    new_schema: dict[str, Any] | None,
) -> list[str]:
    """Los campos de `old_values` que el esquema nuevo ya no declara.

    Se expone aparte de :func:`apply_schema_migration` para que la UI pueda
    decir «se descartaron estos ajustes» en vez de perderlos en silencio.
    """
    props = _properties(new_schema)
    return sorted(set(old_values or {}) - set(props))


__all__ = [
    "SECRET_MUST_BE_VAULT_POINTER",
    "VAULT_POINTER_PREFIX",
    "apply_defaults",
    "apply_schema_migration",
    "dropped_fields",
    "validate_deployment_config",
]
