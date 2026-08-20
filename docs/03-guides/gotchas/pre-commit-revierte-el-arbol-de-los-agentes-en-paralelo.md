---
title: "`git commit` con agentes trabajando en paralelo les revierte el árbol"
symptom: "Un agente encuentra sus ediciones de ficheros trackeados borradas, sin haber ejecutado ningún comando git; los untracked sobreviven"
area: git, pre-commit, trabajo en paralelo
---

# `git commit` con agentes trabajando en paralelo les revierte el árbol

## Síntoma

Un agente que trabaja en el repo informa de esto:

> Sobre las 00:52 un `git checkout -- .` / `git restore` (o equivalente) borró
> **todas** las modificaciones de ficheros trackeados del working tree — las mías
> y las de los demás carriles. Sobrevivieron solo los untracked y lo que estaba
> en el índice. **No fui yo**: no ejecuté ningún comando git destructivo.

Y tiene razón en las dos mitades: el árbol se revirtió, y no lo hizo él.

## Causa raíz

No hubo ningún `git checkout`. Lo hizo **`pre-commit`**, haciendo exactamente lo
que documenta:

```
[WARNING] Unstaged files detected.
[INFO] Stashing unstaged files to C:\Users\…\.cache\pre-commit\patch1787176152-22860.
…hooks…
[INFO] Restored changes from C:\Users\…\.cache\pre-commit\patch1787176152-22860.
```

Para que los hooks vean **solo lo que se va a comitear**, `pre-commit` aparta a
un patch todo lo que está modificado y sin `git add`, y lo devuelve al terminar.
Entre esas dos líneas —que pueden ser **minutos**, si un hook corre mypy sobre el
árbol entero— el working tree está en el estado del índice: para cualquier otro
proceso, todas las modificaciones no indexadas han desaparecido.

Con un humano tecleando no se nota. Con cinco agentes escribiendo a la vez, tres
cosas pasan a la vez:

1. **Ven el árbol revertido** y concluyen, razonablemente, que alguien ejecutó un
   comando destructivo.
2. **Lo que escriban DURANTE la ventana** puede perderse al restaurar el patch,
   que se aplica sobre lo que haya.
3. Si el commit **falla** (un hook en rojo), la restauración ocurre igual, pero
   el agente que la provocó ya está mirando otra cosa y no lo relaciona.

La combinación que lo hace probable en este repo: el hook de mypy corre sobre el
**árbol completo** (~740 ficheros, minutos), así que la ventana es ancha; y un
commit parcial durante una ola falla casi seguro, porque el mypy del árbol ve el
código a medio escribir de OTRO carril.

## Fix

**No comitees mientras haya agentes escribiendo en el árbol.** Es la regla, y no
tiene excepción cómoda: no hay forma de pedirle a `pre-commit` que no aparte los
unstaged sin renunciar a que los hooks vean lo que se comitea.

Si hay que comitear igualmente (por ejemplo, para desbloquear CI):

- Espera a que la ola termine. Es lo barato.
- O usa un **worktree aparte** para el commit (`git worktree add`), que tiene su
  propio working tree y no toca el de los agentes.

Y si ya ha pasado: **comprueba que no se perdió nada** antes de seguir. Los
ficheros nuevos (untracked) sobreviven siempre; lo que hay que verificar son las
MODIFICACIONES a ficheros trackeados. La forma rápida es pedirle a cada agente la
lista de ficheros que tocó y confirmar que el cambio sigue ahí —`grep` del
símbolo que introdujo, no `git status`, que solo dice que el fichero está
modificado, no que lo esté con lo suyo—.

## Por qué no se arregla «desactivando el stash»

`pre-commit` tiene la opción, y es peor: los hooks pasarían a ver el árbol
completo en vez del contenido del commit, así que un hook podría dar verde por
trabajo que NO se está comiteando. Un commit verde que certifica código ajeno es
justo lo que estas guardas existen para impedir.

## Historia

2026-08-20, ola 7. Un intento de comitear `ci.yml` mientras cinco carriles
trabajaban abrió una ventana de minutos (el hook de mypy encontró errores de un
carril a medias, así que el commit falló y el patch se restauró). El carril de la
contaminación entre shards perdió sus tres ediciones sobre ficheros trackeados y
tuvo que reaplicarlas; el resto no llegó a escribir dentro de la ventana. Se
verificó fichero por fichero que no faltaba nada más.
