---
title: "`git checkout --` para deshacer TU mutación borra el trabajo sin comitear de todos los demás"
area: git, verificación, trabajo en paralelo
encountered: 2026-08-12
stack: git
---

## Síntoma

Verificas que un test puede ponerse rojo mutando la implementación, y deshaces la
mutación como parece natural:

```bash
python -c "…"                       # mutar una línea
pytest …                            # rojo: bien, el test sirve
git checkout -- ruta/al/fichero.py  # deshacer la mutación
pytest …                            # ¿5 failed?
```

El verde no vuelve. Sale **peor** que antes de empezar. Y no hay ningún error:
`git checkout` no dice nada.

## Causa raíz

`git checkout -- <fichero>` no deshace _tu último cambio_: **restaura el fichero
desde el índice**, tirando TODAS las modificaciones no comiteadas. Si el fichero
llevaba encima 149 líneas de trabajo de otro agente (o tuyo de hace una hora, o
de una rama en curso), se van con tu mutación de una línea.

La confusión es que en un árbol limpio los dos significados coinciden: si tu
mutación era el único cambio, restaurar desde el índice **es** deshacer tu
mutación. En un árbol con trabajo sin comitear —que es el estado normal cuando
varios agentes trabajan en paralelo, o cuando estás a punto de comitear una
tanda— dejan de coincidir, y el comando hace silenciosamente mucho más de lo que
pedías.

Agravante: la señal que te avisa es engañosa. Los tests **siguen ahí** (viven en
otro fichero), así que el rojo posterior parece «la mutación no se deshizo bien»
en vez de «he borrado la implementación». En el caso real, los 5 rojos eran
exactamente los 5 tests nuevos corriendo contra el generador revertido.

## Fix

**Deshaz tu mutación exactamente como la hiciste, en sentido inverso.** Si la
aplicaste con un reemplazo de texto, revierte con el reemplazo simétrico:

```python
s = p.read_text(encoding="utf-8")
p.write_text(s.replace(MUTADO, ORIGINAL, 1), encoding="utf-8")
```

O guarda el original antes y restáuralo desde ahí:

```bash
cp fichero.py "$SCRATCH/fichero.py.bak"   # el scratchpad, NO el repo
# … mutar, correr, ver el rojo …
cp "$SCRATCH/fichero.py.bak" fichero.py
```

Y **comprueba que la reversión funcionó** volviendo a correr los tests: el verde
es la prueba, no la ausencia de error del comando.

Si el trabajo ya se perdió: los **tests suelen sobrevivir** (están en otro
fichero) y son la especificación exacta de lo que hay que reconstruir. Si lo
escribió un subagente, pídele que lo rehaga — conserva el contexto entero y
tarda mucho menos que reconstruirlo leyendo su informe.

## Lo que NO hay que hacer

**`git checkout --`, `git restore` ni `git stash` sobre un fichero que no sabes
si tiene trabajo ajeno encima.** Antes de cualquiera de los tres, mira qué te vas
a llevar por delante:

```bash
git diff --stat ruta/al/fichero.py   # ¿149 líneas que no son tuyas?
```

## La clase de problema, que volverá

Es la misma familia que el error simétrico de `git add .`: comandos cuyo alcance
es **el fichero entero** usados con intención de **un cambio concreto**. Con
varios agentes escribiendo en paralelo sobre un árbol sin comitear, el alcance
por defecto de git deja de ser el que uno tiene en la cabeza.

Ocurrió **dos veces el mismo día** (2026-08-12): primero deshaciendo un
reformateo propio que resultó llevarse el trabajo del carril de i18n, y después
deshaciendo esta mutación de auditoría. La primera vez parece un descuido; la
segunda ya es el patrón, y por eso está escrito aquí.

Regla práctica: **antes de revertir, `git diff --stat` del fichero**. Si el
número no es el tuyo, no es tu reversión.
