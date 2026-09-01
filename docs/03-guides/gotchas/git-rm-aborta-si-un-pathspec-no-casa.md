---
title: "`git rm` aborta entero si UN pathspec no casa (`git ls-files` no)"
area: git
encountered: 2026-09-01
stack: Git for Windows 2.53, git ≥ 1.x (comportamiento documentado de `git rm`)
---

## Síntoma

Escaneas con `git ls-files` qué hay versionado que casa con una lista de
pathspecs, ves resultados, y a continuación **actúas sobre esa misma lista** con
`git rm`. No pasa nada: el índice queda exactamente igual que estaba.

```bash
git ls-files -- ':(glob)**/vendor/**' ':(glob)**/node_modules/**' \
                ':(glob)**/.venv/**'  ':(glob)**/venv/**'
# vendor/autoload.php
# vendor/pkg/a.php
# rc=0

git rm -r --cached --quiet -- ':(glob)**/vendor/**' ':(glob)**/node_modules/**' \
                              ':(glob)**/.venv/**'  ':(glob)**/venv/**'
# fatal: pathspec ':(glob)**/node_modules/**' did not match any files
# rc=128

git ls-files
# app/Home.php
# vendor/autoload.php   <-- sigue ahí: NO se retiró nada
# vendor/pkg/a.php
```

Lo incómodo es lo bien que se disfraza. En el proyecto donde apareció
—des-versionar los directorios de dependencias al cerrar una tarea, con los
cuatro nombres que declara el catálogo de runtimes— **en un proyecto PHP la
operación no ocurría NUNCA**: siempre había tres nombres (`node_modules`,
`.venv`, `venv`) que ese repo no tiene. El escaneo previo encontraba
perfectamente los ficheros, el log decía cuántos eran, y el trabajo no se hacía.

## Causa raíz

**`git rm` exige que TODOS los pathspecs casen con algo; `git ls-files` no.**
Es la regla general de los comandos que actúan (`git rm`, `git add`): un pathspec
POSITIVO que no encuentra nada es un error, y el comando aborta **completo**, sin
aplicar tampoco los que sí casaban. `git ls-files` es un comando de consulta:
devuelve lo que hay y calla sobre lo que no.

Medido con git 2.53:

| comando                               | pathspec que no casa | rc  | efecto                    |
| ------------------------------------- | -------------------- | --- | ------------------------- |
| `git ls-files -- <4 pathspecs>`       | 3 de 4               | 0   | devuelve los del que casa |
| `git rm -r --cached -- <4 pathspecs>` | 3 de 4               | 128 | **no retira nada**        |
| `git add -- 'no-existe/**'`           | 1 de 1               | 128 | no añade nada             |
| `git add -A -- . ':(exclude,glob)…'`  | 2 de 2 (exclusiones) | 0   | añade normal              |

La última fila es la que cierra la trampa: los pathspecs de **exclusión**
(`:(exclude)`) están exentos de la regla. Por eso la MISMA lista de patrones
funciona sin quejarse en el `git add -A` que los excluye y revienta en el
`git rm` que los retira — y uno da por hecho que si sirve en un sitio sirve en el
otro.

## Fix

**Filtrar los pathspecs a los que de verdad están presentes**, usando la salida
del `ls-files` que ya se hizo para saber cuántos ficheros hay:

```python
versionados = [r for r in salida.split("\0") if r]          # del ls-files
presentes = {p for ruta in versionados for p in ruta.split("/") if p in conjunto}
a_retirar = [f":(glob){patron}" for patron in _patrones(sorted(presentes))]
_run_git("rm", "-r", "--cached", "--quiet", "-f", "--", *a_retirar, cwd=worktree)
```

**Y no con `--ignore-unmatch`**, que es la opción que git ofrece justo para esto.
La razón es de diagnóstico: `--ignore-unmatch` hace que el comando salga con 0
pase lo que pase, así que un fallo de verdad (permisos, índice bloqueado, un
`.git` que ya no está) también saldría con 0 y el des-versionado seguiría sin
ocurrir, ahora ya sin ninguna señal. Filtrando, un `rc != 0` sigue significando
**avería**, y se registra como tal.

Ver `_desversionar_dependencias` en `apps/workers/src/workers/plan_git.py`.

## Cómo verificar el fix

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/test_las_dependencias_no_se_versionan.py -q
```

Comprobado quitando el filtro (2026-09-01): mueren los tres casos que versionan
**sólo `vendor/`** —`test_un_vendor_ya_versionado_sale_del_indice`,
`test_desversionar_es_un_cambio_y_produce_commit` y
`test_queda_registrado_cuantos_ficheros_y_de_donde`—, o sea el proyecto PHP real.
Y ojo con el que NO se entera: `test_cada_directorio_del_catalogo_queda_cubierto`
pasa igual sin filtro, porque crea un fichero dentro de **cada** directorio
declarado y entonces los cuatro pathspecs casan. Un arnés demasiado completo es
justo el que no ve esta trampa.

A mano, sobre un repo de usar y tirar:

```bash
git rm -r --cached --quiet -- ':(glob)**/vendor/**' ':(glob)**/node_modules/**'
echo $?   # 128 si no hay node_modules/, y el índice intacto
```

## La clase de problema, que volverá

**Escanear con un comando de consulta y actuar con uno de mutación usando los
mismos argumentos.** Los dos aceptan la misma sintaxis de pathspec, así que la
lista se comparte con toda naturalidad; lo que no se comparte es el
comportamiento ante lo que no casa. Antes de reutilizar una lista de pathspecs
entre `ls-files`/`ls-tree` y `rm`/`add`/`checkout`, comprueba el `rc` con un
pathspec que sobre.
