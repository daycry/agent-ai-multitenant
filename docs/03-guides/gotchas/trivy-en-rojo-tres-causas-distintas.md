---
title: "Trivy se pone rojo solo, y por tres causas que se confunden entre sí"
area: docker, ci, seguridad
encountered: 2026-08-14
stack: Trivy, GitHub Actions (cache type=gha), Debian/Ubuntu base images
---

## Síntoma

El job «Build runtime templates» pasa un día y **falla al siguiente sin que nadie
haya tocado un Dockerfile**. Medido: verde el 2026-08-13 a las 23:33, rojo el 14 a
las 08:53, con `git show --stat` confirmando que el commit intermedio no tocó
ninguna de las cinco plantillas que fallaron.

Es esperable y no es un fallo del gate: Trivy **refresca su base de vulnerabilidades
en cada corrida**. Un CVE publicado de madrugada pone en rojo una imagen idéntica a
la de ayer.

## Causa raíz — y aquí está la trampa: son TRES, y el arreglo de una no sirve para las otras

**(1) El paquete viene de la base.** Refrescar el digest del `FROM` lo cierra. Es el
caso fácil y el que todo el mundo asume.

**(2) El paquete lo instala NUESTRO `apt-get`… y la capa está CACHEADA.** Fue el caso
de `ruby-rspec` y los dos de PHP con `CVE-2026-6473` (`libpq`): Debian ya había
publicado el fix, pero el workflow usa `cache-from: type=gha` y ese `RUN` salía
`#10 CACHED`, así que **el índice de apt jamás se refrescó** y se reinstaló el árbol
de paquetes del día anterior.

> Lo contraintuitivo: **añadir `apt-get upgrade` no arregla este caso.** Con la capa
> entera cacheada, ese comando ni se llega a ejecutar. Lo que hace falta es
> invalidar la capa — y mover el digest de la base invalida ésa y todas las de
> abajo.

**(3) El binario no pertenece a ningún paquete.** El caso de `java-gradle`: los ocho
HIGH estaban los ocho en `/usr/bin/pebble`, un binario Go (stdlib 1.26.5) que
Canonical deposita dentro de la imagen OCI de Ubuntu 26.04 «resolute». `dpkg -S
/usr/bin/pebble` **no encuentra dueño**. Aquí no sirve nada de lo anterior:

- no hay digest nuevo que poner (`gradle:9-jdk21` seguía resolviendo al mismo);
- la caché no pinta nada (ese Dockerfile no instala nada con apt);
- `apt-get upgrade` no lo toca, porque no hay paquete que actualizar.

La salida fue cambiar a la variante `-noble` (Ubuntu 24.04 LTS), que no lleva ese
binario.

## Cómo distinguirlas en dos minutos

Del informe de Trivy, mira la columna del paquete y pregúntate **de dónde salió**:

```bash
docker run --rm <imagen> dpkg -S /ruta/al/binario   # ¿tiene dueño?
docker manifest inspect <base>:<tag>                # ¿hay digest nuevo?
```

Y en el log del build de CI, busca `CACHED` en el `RUN` que instala el paquete: si
está, la causa es (2) por mucho que el `apt-get` esté bien escrito.

## Lo que NO hay que hacer

**Recortar `--severity`, añadir `.trivyignore`, meter `continue-on-error` o soltar
el pin del digest.** Los digests están fijados a propósito (higiene de cadena de
suministro: nunca `:latest`); el arreglo es fijar el digest NUEVO, no dejar de
fijar. Un CVE sin fix aguas arriba es la única excepción legítima, y va escrita con
el CVE, la imagen y si el código vulnerable se ejecuta siquiera en nuestro uso.

## La decisión de fondo, que sigue abierta

Un gate que depende de una base de datos externa que cambia sola **no puede estar
establemente verde**. O se mantiene bloqueante y se asume un mantenimiento
periódico, o se mueve a un job diario que abra un aviso en vez de bloquear PRs. Las
dos son defendibles; lo que no lo es es acostumbrarse a verlo rojo, porque entonces
deja de distinguir el hallazgo nuevo del de ayer.
