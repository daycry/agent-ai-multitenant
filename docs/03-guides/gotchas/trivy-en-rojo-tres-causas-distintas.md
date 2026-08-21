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

## Actualización 2026-08-19: la salida de (2) que este documento daba NO siempre existe

Cuatro templates (`python-pytest`, `node-jest`, `node-vitest`, `rust-cargo`) y el
`api-server` cayeron con **CVE-2026-53615**, la familia `util-linux` de Debian 13.
Caso (2) de manual, y `rust-cargo` lo demostró de la forma más clara posible:
**ya tenía** su `apt-get -y upgrade` desde hacía semanas, y el log dice

```text
#12 [2/5] RUN apt-get update  && apt-get -y upgrade  && rm -rf /var/lib/apt/lists/*
#12 CACHED
```

Hasta aquí, lo que este documento ya contaba. Lo nuevo es que **el remedio que
proponía —«mover el digest de la base invalida ésa y todas las de abajo»— no
estaba disponible**: el tag más reciente de las tres bases seguía trayendo el
paquete vulnerable.

```console
$ docker run --rm python:3.12-slim apt-cache policy util-linux
util-linux:
  Installed: 2.41-5
  Candidate: 2.41.5-0+deb13u1
     2.41.5-0+deb13u1 500 http://deb.debian.org/debian-security trixie-security/main
```

El parche existía y estaba publicado, pero **en el repositorio de Debian, no en la
imagen**. Refrescar el digest habría cambiado el digest y no la vulnerabilidad.

**El arreglo, que son dos mitades y con una sola no basta:**

1. **La capa de parcheo, donde falta.** `python-pytest`, `node-vitest`, `node-jest`
   y `api-server` no tenían ninguna: entregaban los paquetes de la base tal cual.
2. **Que esa capa no se cachee.** `docker/build-push-action` acepta el nombre de la
   etapa:

   ```yaml
   cache-from: type=gha,scope=runtime-${{ matrix.template }}
   cache-to: type=gha,mode=max,scope=runtime-${{ matrix.template }}
   no-cache-filters: runtime
   ```

   La etapa final se reconstruye siempre; las `builder` —las compilaciones caras—
   siguen cacheadas.

Se descartó el `ARG SECURITY_REFRESH` con el año-semana (invalida la capa al
cambiar de semana): funciona, pero deja una ventana de hasta siete días y añade
una pieza que se pudre en silencio si alguien deja de pasar el argumento.

**Y no se suprimió en `.trivyignore`**, que es para vulnerabilidades CON fix que se
decide no aplicar todavía. Aquí el fix estaba a un `apt-get upgrade` de distancia.

Verificado en local antes de tocar CI: imagen reconstruida →
`util-linux 2.41.5-0+deb13u1`, y `trivy image --ignore-unfixed --severity
HIGH,CRITICAL` sale con **código 0**.

> La moraleja incómoda: esta trampa **ya estaba escrita aquí** desde el 2026-08-14,
> con el diagnóstico correcto. Lo que faltaba era haber aplicado el arreglo a los
> Dockerfiles, no haberlo entendido. Un gotcha documentado y no aplicado avisa dos
> veces del mismo incendio.

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
