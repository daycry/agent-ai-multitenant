---
title: Política de documentación bilingüe
last_updated: 2026-08-21
status: published
docs_language: es
audience: contributors
---

[English](./bilingual-docs.md) · **Español**

# Política de documentación bilingüe

Cómo este repositorio se mantiene bilingüe sin romper nunca un enlace: el inglés
es el idioma canónico, el castellano viaja a su lado en un fichero hermano
`.es.md`, y una guarda impide que un par se quede a medias.

Decisión del operador del **2026-08-21**: todo documento nuevo, el README, el
changelog y el sitio publicado salen en **inglés y castellano**, con el inglés
como versión canónica.

## La regla, en una línea

`foo.md` es el documento **canónico en inglés**; `foo.es.md` es su traducción al
**castellano**. Nada más marca idioma.

## Convención de nombres

| Camino                | Idioma                      | Papel                                                      |
| --------------------- | --------------------------- | ---------------------------------------------------------- |
| `CHANGELOG.md`        | inglés                      | canónico — el que lee primero una herramienta o un extraño |
| `CHANGELOG.es.md`     | castellano                  | traducción, enlazada desde la cabecera del canónico        |
| `foo.md` (sin pareja) | lo que diga `docs_language` | todavía no bilingüe — un estado conocido, no un error      |

Las dos mitades **se enlazan entre sí en la cabecera** —las primeras líneas del
documento, donde la página ponga su título— para que quien aterrice en la
equivocada salga con un clic. La guarda lo comprueba.

El campo `docs_language:` del frontmatter sigue haciendo exactamente lo que hacía:
declara en qué idioma está escrito un fichero, y es la autoridad cuando el nombre
no puede decirlo (un documento sin hermano). El sufijo del nombre responde a otra
pregunta —_cuál de las dos mitades es ésta_— y nunca se contradicen, porque
`X.es.md` lleva siempre `docs_language: es`.

## Por qué un sufijo y no `docs/en/` + `docs/es/`

La partición por carpetas es la opción obvia, y es la única que aquí no se puede
adoptar de forma incremental.

Este repositorio tiene **los ADR, el catálogo de gotchas, el roadmap entero y las
siete carpetas canónicas** escritos en castellano. Medido el 2026-08-21: 666
ficheros Markdown bajo `docs/`, 968k palabras, de los que 326 declaran
`docs_language: es` y tres declaran `en`. Con más de mil enlaces internos `.md`
entre ellos y varias
guardas vigilando esos enlaces
([`tests/docs/test_docs_internal_links.py`](../../tests/docs/test_docs_internal_links.py)),
la estructura de carpetas (`tests/integration/test_docs_structure_guardrail.py`) y
el índice de guías (`tests/docs/test_docs_training_model.py`). Mover un documento
a `docs/es/` reescribe todos los enlaces que apuntan a él, y hasta que se mueva el
último el corpus vive en dos estructuras a la vez — que es exactamente el estado a
medias que una política bilingüe viene a evitar.

El fichero hermano tiene la propiedad que le falta a la partición por carpetas:
**traducir un documento no rompe nunca un enlace.**

```text
antes:   docs/03-guides/foo.md            (castellano, N enlaces entrantes)
paso 1:  git mv foo.md foo.es.md          (el castellano se aparta)
paso 2:  se escribe foo.md en inglés      (el canónico toma el nombre desnudo)
después: los N enlaces entrantes siguen resolviendo — y ahora caen en el canónico
```

Ni una reescritura de enlaces, nunca, en ninguna dirección. El nombre desnudo es
una dirección estable cuyo _contenido_ migra de castellano a inglés documento a
documento, y el lector en castellano sigue el enlace cruzado de la cabecera.

Hay una segunda razón, más estrecha pero no negociable: **los nombres que
reconoce la plataforma son fijos.** GitHub renderiza `README.md`, las herramientas
y la convención Keep a Changelog buscan `CHANGELOG.md`, `LICENSE` no lleva sufijo.
Un sufijo de idioma en ésos pierde el comportamiento de la plataforma, así que el
nombre desnudo tiene que pertenecer a un idioma — y la decisión del operador dice
que ese idioma es el inglés.

## Qué es bilingüe hoy y qué no

**Bilingüe ya** — cada par lo valida la guarda:

- el `README.md` y el `CHANGELOG.md` de la raíz;
- el sitio publicado: [`mkdocs.yml`](../../mkdocs.yml) corre
  `mkdocs-static-i18n` en **modo `suffix`**, que es exactamente esta convención —
  nombre desnudo para el inglés, `foo.es.md` para el castellano, un solo sitio con
  selector de idioma y ni un documento movido;
- `docs/index.md`, la home del sitio;
- los diagramas de arquitectura de `docs/01-overview/`
  ([`03-diagrams.md`](../01-overview/03-diagrams.md)). Aterrizaron el mismo día que
  esta política, en otro carril y llamándose `03-diagrams.en.md`; se realinearon al
  nombre desnudo antes de comitear, así que el inventario de desviaciones está
  vacío — el mecanismo se queda, la entrada no.
- sus seis diagramas Mermaid los vigila además contra el código
  [`tests/docs/test_diagram_guards.py`](../../tests/docs/test_diagram_guards.py),
  que también rechaza un par cuyas dos mitades dejen de dibujar los mismos ids de
  nodo — la regla bilingüe aplicada a un dibujo y no a la prosa.
- esta política.

**No bilingüe en esta ola**, y a propósito:

- los ADR de `05-architecture-decisions/` (160 el 2026-08-21);
- los gotchas de `03-guides/gotchas/` (108 en la misma fecha);
- el roadmap de `docs/roadmap/`;
- el resto de las siete carpetas canónicas.

Se quedan sólo en castellano en su camino canónico, declarado por
`docs_language: es`. Eso es un **backlog con forma**, no un hueco: cada uno de
esos documentos pasa a bilingüe con los dos pasos de arriba, por su cuenta, sin
coordinarse con ningún otro.

## Cómo añadir un documento nuevo

1. Escribe la versión inglesa en el nombre desnudo (`foo.md`), `docs_language: en`.
2. Escribe la versión castellana en `foo.es.md`, `docs_language: es`.
3. Pon el enlace cruzado en las dos cabeceras, en las primeras líneas del documento.
4. Si el documento es una guía, lista **las dos** mitades en
   [`README.md`](./README.md) — la guarda del índice exige que todo `.md` de la
   carpeta aparezca ahí.

Un documento que por ahora va a quedarse sólo en castellano no es una infracción:
se escribe en el nombre desnudo con `docs_language: es` y sin hermano. Sólo los
documentos de la raíz están obligados a ser bilingües, porque son los que lee
primero un extraño.

## Cómo traducir un documento que ya existe

```bash
git mv docs/03-guides/foo.md docs/03-guides/foo.es.md
# y luego se escribe el canónico inglés en docs/03-guides/foo.md
```

Añade el enlace cruzado a las dos cabeceras y pon `docs_language: es` en el
fichero movido si no lo decía ya. No toques los enlaces entrantes: apuntan al
nombre desnudo, que sigue ahí.

## La guarda

[`tests/docs/test_bilingual_docs.py`](../../tests/docs/test_bilingual_docs.py)
hace cumplir la parte de esta política que un humano olvida:

- todo documento Markdown de la raíz es bilingüe, salvo los ficheros de trabajo
  nombrados en la lista de exenciones (con su motivo escrito en el test);
- ninguna traducción huérfana: un `.es.md` cuya mitad canónica no existe;
- ninguna segunda convención de nombres: un fichero `.en.md` es un canónico mal
  nombrado, no una alternativa, y sólo se toleran las desviaciones declaradas el
  2026-08-21;
- las dos mitades se enlazan entre sí en las primeras líneas del cuerpo — salvo
  donde el selector de idioma del propio sitio hace ese trabajo, que va declarado,
  razonado, y sólo vale mientras el documento le diga al lector que el selector
  está ahí;
- las dos mitades tienen la misma estructura de encabezados, para que una
  traducción no pueda perder una sección en silencio;
- los enlaces internos `.md` de los documentos bilingües de la raíz resuelven —
  la guarda de enlaces anterior sólo recorre `docs/`;
- los dos inventarios congelados fallan ante una entrada muerta, para que una
  desviación arreglada no pueda seguir declarada;
- y una aserción de no-vacuidad, porque un descubrimiento que no encuentra nada
  pasaría verde para siempre (ver
  [verificar-antes-de-implementar.md](./verificar-antes-de-implementar.md) §4).

Todas se verificaron en rojo antes de darlas por buenas: nueve mutaciones —borrar
una mitad castellana, añadir una sección a una sola mitad, romper un enlace
cruzado, añadir un `.en.md`, romper un enlace interno, meter una entrada muerta en
cada uno de los dos inventarios, y quitar el glob de la raíz a cada una de las dos
puertas de markdown— pusieron en rojo exactamente el test que tocaba y ningún
otro.

El conjunto de documentos bilingües se **descubre**, no se lista a mano: el día
que alguien añada `foo.es.md`, el par se valida desde ese commit. Lo que se lista
a mano es la _exención_ —los ficheros de la raíz que siguen monolingües—, así que
la lista sólo puede encogerse por accidente, nunca crecer por accidente.

## Esto es de la documentación, no del producto

El principio 12 de `CLAUDE.md` («idiomas soportados: ES + EN únicamente») habla de
lo que dice el **producto**: los diccionarios i18n del panel, la persona del
agente, la documentación generada del proyecto de un tenant. Esa regla no cambia,
ni cambia la de [`../README.md`](../README.md) según la cual el `/docs/` de un
_proyecto generado_ se ciñe al único idioma que su proyecto declara.

Esta política es de la **documentación del propio repositorio de plataforma**, que
la leen quienes contribuyen y quien encuentre el repo público. Ésa es bilingüe.

## Relacionado

- [`../README.md`](../README.md) — estructura canónica de `/docs` y reglas de
  formato.
- [`../context/conventions.md`](../context/conventions.md) — convenciones de
  código, commits y Markdown.
- [`verificar-antes-de-implementar.md`](./verificar-antes-de-implementar.md) — por
  qué una guarda que no puede fallar no es una guarda.
