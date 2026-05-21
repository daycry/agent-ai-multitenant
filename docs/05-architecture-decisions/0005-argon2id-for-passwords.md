---
adr: "0005"
title: Argon2id para hashing de passwords
status: accepted
date: 2026-05-20
deciders: System Architect
phase: 00-fundaciones
---

# ADR 0005 — Argon2id para hashing de passwords

## Contexto

El sistema almacena `password_hash` en `users.password_hash`. La
elección del algoritmo determina:

- Resistencia ante ataques offline (un dump de la BD).
- Coste por verificación (impacto en el throughput de login).
- Compatibilidad con futuras subidas de parámetros sin invalidar
  todos los hashes existentes.

Las alternativas comunes son **bcrypt**, **scrypt**, **PBKDF2**,
**Argon2id** y derivados específicos del proveedor.

## Decisión

**Argon2id** con parámetros conservadores recomendados por OWASP
(2023):

- `time_cost = 3` (iteraciones).
- `memory_cost = 65536` (64 MiB).
- `parallelism = 4` (hilos).
- `type = ID` (Argon2id, modo híbrido resistente a ataques
  side-channel y a hardware paralelo masivo).

La librería usada: **`argon2-cffi`** (bindings a la implementación
de referencia de OWASP).

## Alternativas descartadas

1. **bcrypt.** Bueno y maduro, pero (a) limita la password a 72
   bytes (silencioso), (b) `memory_cost` no es configurable
   —vulnerable a ASIC-based attacks—, (c) más lento por iteración
   que Argon2id con el mismo nivel de seguridad.
2. **scrypt.** Resistencia a hardware buena pero parámetros menos
   intuitivos; recomendación OWASP es preferir Argon2id cuando esté
   disponible.
3. **PBKDF2.** El más portable, pero el más débil contra GPUs / ASICs.
   Reservado para casos donde Argon2 no está disponible (no es
   nuestro caso, Python tiene bindings sólidos).

## Consecuencias

Positivas:

- ~140 ms por verificación en un laptop moderno → rate-limit del
  login (5/15 min) ya lo hace impracticable para fuerza bruta.
- `_hasher.check_needs_rehash()` permite **migrar a parámetros más
  fuertes** transparentemente: tras un login exitoso, si los
  parámetros del hash almacenado son inferiores a los actuales, se
  re-hashea con los nuevos.
- Compatible con OWASP / NIST recommendations.

Negativas / cuidados:

- 64 MiB de RAM por verificación. Multiplicado por concurrencia
  en login es no-trivial. Vamos a vigilarlo con métricas en Fase 12.
- Si un día migramos a otro algoritmo, hay que mantener
  compatibilidad con los hashes existentes (verificar contra varios
  hashers). Argon2 lo facilita porque su formato encoded incluye
  todos los parámetros.

## Referencias

- `apps/api-server/src/api_server/auth/passwords.py`.
- OWASP Password Storage Cheat Sheet (2023).
- Documento maestro, sección 17.2.
