---
title: "ADR 0143: Clave de cifrado de los seeds TOTP — propia, no acoplada a la de SSO"
status: accepted
date: 2026-07-31
deciders: [claude-code]
relates_to: [0047, 0136, 0144]
plan_referenced: prod-05-rotacion-claves
task: task_prod05_03
---

# ADR 0143: Clave de cifrado de los seeds TOTP — propia, no acoplada a la de SSO

> **Estado: `accepted`.** Decisión **técnica** (radio de impacto de una rotación),
> no de producto: no cambia ninguna funcionalidad visible, ningún flujo de
> usuario y ningún precio. Por eso se decide aquí en vez de dejarse `proposed`.
> **Opción elegida: A — clave propia `API_SERVER_MFA_ENCRYPTION_KEY(S)`, con
> herencia del anillo SSO cuando no está configurada.**

## Contexto verificado (2026-07-31)

`apps/api-server/src/api_server/auth/mfa/secrets.py` era, literalmente, un envoltorio:

```python
from api_server.auth.sso.secrets import decrypt_client_secret, encrypt_client_secret
```

Los seeds TOTP de **todos** los usuarios (`user_mfa_totp.secret_encrypted`) se
cifraban con la clave derivada de `API_SERVER_SSO_ENCRYPTION_KEY`. El comentario
original defendía la decisión con un argumento razonable — «una sola historia de
rotación, un solo sitio donde cablear Vault» — y ese argumento tenía un coste que
nadie había puesto en números hasta la auditoría (gap2-4).

El coste es este: un operador que rota `API_SERVER_SSO_ENCRYPTION_KEY` cree estar
tocando **los client secrets de OIDC guardados**. Lo que en realidad toca es eso
**más todos los seeds TOTP de la plataforma**. Y con
`API_SERVER_ADMIN_REQUIRE_MFA=true` — el default fuera de dev
(`config.py`, sección de admin hardening) — eso deja a **todos los System Admin
fuera de `/admin/*`**, es decir, fuera de la superficie desde la que arreglarían el
problema.

Dos secretos con radios de impacto distintos y cadencias de rotación distintas no
pueden compartir clave.

## Opciones

### Opción A — clave propia para MFA (elegida)

`API_SERVER_MFA_ENCRYPTION_KEY(S)` como familia independiente, con **fallback al
anillo SSO** cuando ninguna de las dos variables está puesta.

- El fallback es la bisagra de compatibilidad: sin él, desplegar este ADR
  convertiría todos los seeds existentes en `InvalidToken`, es decir, provocaría
  exactamente el lockout que pretende evitar.
- Rotar SSO deja de tocar MFA y viceversa.
- Adoptar la clave propia en un despliegue vivo es la rotación de tres pasos
  normal (`API_SERVER_MFA_ENCRYPTION_KEYS=<nueva>,<clave-sso-actual>` → deploy →
  `reencrypt-secrets --families mfa` → quitar la vieja → deploy), documentada en
  el runbook y en el docstring del módulo.
- Coste: un secreto más que custodiar y una entrada más en el runbook.

### Opción B — mantener el acoplamiento, documentándolo

- Coste cero de implementación.
- Deja intacto el hallazgo: la única mitigación posible sería una advertencia en
  el runbook, y una advertencia no impide que la rotación de una clave «de SSO»
  bloquee a los administradores. La auditoría clasificó gap2-4 como _high_
  precisamente porque el fallo es silencioso hasta el siguiente login.

## Decisión

**Opción A.** El argumento decisivo no es la elegancia: es que el modo de fallo de
B se manifiesta **después** de la rotación, cuando ya no queda nadie con acceso
administrativo para revertirla. Un secreto más que custodiar es barato; un
lockout total de administración durante una ventana de mantenimiento no lo es.

El fallback al anillo SSO hace que la decisión sea **desplegable sin ninguna
acción del operador**: nada cambia hasta que alguien decide separar las claves.

## Consecuencias

- `Settings.mfa_encryption_key_ring` resuelve, por orden: la lista dedicada, la
  clave dedicada, el anillo SSO. `Settings.mfa_key_is_dedicated` dice cuál de los
  dos mundos está vigente, y el runbook se ramifica sobre esa propiedad.
- `user_mfa_totp` es una familia propia (`mfa`) en `TARGETS` de
  `api_server.cli.reencrypt_secrets`, así que se puede re-cifrar sola:
  `python -m api_server.cli reencrypt-secrets --families mfa`.
- La guarda de secretos dev en staging/prod solo nombra
  `API_SERVER_MFA_ENCRYPTION_KEY(S)` cuando la clave es dedicada; si hereda, no
  tiene sentido señalar una variable que el operador no ha puesto.
- **El break-glass del lockout MFA sigue siendo necesario** y está documentado en
  `docs/06-runbooks/05-key-rotation.md`: separar las claves reduce la
  probabilidad del lockout, no la elimina (un fallo de re-cifrado sigue pudiendo
  dejar seeds ilegibles).

## Condiciones de cierre

1. El operador decide si adopta la clave dedicada en su despliegue, o se queda con
   la herencia. **Ambas son configuraciones soportadas**; este ADR no obliga a
   migrar.
2. Si la adopta: seguir los tres pasos del runbook, no dos.
