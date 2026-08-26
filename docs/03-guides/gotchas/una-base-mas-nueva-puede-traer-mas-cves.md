---
title: "Una base más nueva puede traer MÁS vulnerabilidades que la que sustituye"
area: docker, seguridad, ci
encountered: 2026-08-26
stack: Dependabot (ecosistema docker), Trivy 0.70, Alpine
---

## Síntoma

Dependabot abre un PR que sube la base de cuatro imágenes de `alpine:3.20` a
`alpine:3.24`. Suena a lo que uno quiere: una base más reciente, con más parches.
El PR se lee en diez segundos y se mergea en cinco.

Y el gate de Trivy se pone rojo en imágenes que estaban verdes.

## Causa raíz

**«Más nueva» y «con menos CVE abiertos» son cosas distintas, y no están
correlacionadas de forma fiable.** Una rama nueva de la distribución empaqueta
versiones más recientes de sus librerías, y una versión más reciente puede tener
un CVE publicado _que la vieja no tiene_ — porque el fallo se introdujo después,
o porque la rama vieja ya recibió el backport y la nueva todavía no.

Medido en este repo el 2026-08-26, con la misma versión de Trivy y la misma base
de datos, en el mismo minuto:

```console
$ trivy image --ignore-unfixed --severity HIGH,CRITICAL alpine@sha256:d9e853e8…   # 3.20.10, la que había
0  Clean (no security findings detected)

$ trivy image --ignore-unfixed --severity HIGH,CRITICAL alpine@sha256:28bd5fe8…   # 3.24, la propuesta
Total: 2 (HIGH: 2, CRITICAL: 0)
libcrypto3  CVE-2026-14456  HIGH  fixed  3.5.7-r0 -> 3.5.8-r0
libssl3     (idem)
```

La 3.20 traía OpenSSL sin ese fallo. La 3.24 trae una rama de OpenSSL que sí lo
tiene, con el fix publicado upstream (`3.5.8-r0`) pero **todavía no horneado en
la imagen** — que es exactamente la causa (2) de
[trivy-en-rojo-tres-causas-distintas.md](./trivy-en-rojo-tres-causas-distintas.md),
vista desde el otro lado.

## Por qué muerde justo aquí

Porque el sesgo de lectura juega en contra. Un PR titulado
`Bump the docker-bases group` con un digest más alto **se aprueba solo**: nadie
pide medición para «actualizar la base», del mismo modo que nadie la pide para
«actualizar una dependencia a la última». Es el único tipo de PR donde el
resultado esperado y el medido divergen sin que nada chirríe.

Y el daño se reparte: en el caso medido, las cuatro imágenes afectadas incluían
los dos tinyproxy, uno de ellos la **única salida a internet del contenedor donde
corre código no confiable** (ADR 0019, Principio Rector 2).

## Fix

No hay nada que arreglar en el repo: **hay que no mergearlo**, y decirlo con el
número. El PR se cierra citando las dos salidas de Trivy, y vuelve solo cuando
Dependabot proponga un digest que ya lleve el paquete corregido.

Lo que sí conviene evitar es el atajo: suprimir el CVE en `.trivyignore` para
poder mergear la base nueva convierte una regresión medida en una regresión
silenciosa. `.trivyignore` es para vulnerabilidades **con fix que se decide no
aplicar**; aquí el fix se aplica solo, esperando.

## Cómo verificar antes de mergear una base

Dos escaneos y una comparación. Cuesta un minuto y es la única forma de saberlo:

```console
$ viejo=$(git show master:ruta/al/Dockerfile | grep -oE 'sha256:[0-9a-f]+' | head -1)
$ nuevo=$(gh pr diff <N> | grep -E '^\+FROM' | grep -oE 'sha256:[0-9a-f]+' | head -1)
$ for d in "$viejo" "$nuevo"; do
>   trivy image --ignore-unfixed --severity HIGH,CRITICAL "alpine@$d" | grep -E 'Total:|Clean'
> done
```

Si el nuevo sale peor que el viejo, el PR no es una actualización: es una
regresión con buena presentación.

> **Nota de alcance.** Esto vale para cualquier bump de imagen base, no sólo
> Alpine. Y aplica igual al caso simétrico —una base más nueva que sí arregla
> CVEs— que es el habitual: la lección no es «no subir bases», es «medir antes,
> porque el sentido común acierta casi siempre y el casi es el que cuesta».
