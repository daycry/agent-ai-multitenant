---
title: Bilingual documentation policy
last_updated: 2026-08-21
status: published
docs_language: en
audience: contributors
---

**English** · [Español](./bilingual-docs.es.md)

# Bilingual documentation policy

How this repository stays bilingual without ever breaking a link: English is the
canonical language, Spanish rides alongside it in a `.es.md` sidecar, and a guard
refuses to let a pair go half-written.

Operator decision of **2026-08-21**: every new document, the README, the
changelog and the published site ship in **English and Spanish**, with English
as the canonical version.

## The rule, in one line

`foo.md` is the **English canonical** document; `foo.es.md` is its **Spanish**
translation. Nothing else is a language marker.

## Naming convention

| Path               | Language                      | Role                                                     |
| ------------------ | ----------------------------- | -------------------------------------------------------- |
| `CHANGELOG.md`     | English                       | canonical — the version a tool or a stranger reads first |
| `CHANGELOG.es.md`  | Spanish                       | translation, linked from the canonical header            |
| `foo.md` (no pair) | whatever `docs_language` says | not yet bilingual — a known state, not an error          |

Both halves **link to each other in the header** — the first lines of the
document, wherever the page puts its title — so a reader who lands on the wrong
one leaves in a single click. The guard checks it.

The `docs_language:` frontmatter field keeps doing exactly what it did before: it
declares what language a file is written in, and it is the authority when the
filename cannot say (a document with no sidecar). The filename suffix answers a
different question — _which half of a pair is this_ — and the two never disagree,
because `X.es.md` always carries `docs_language: es`.

## Why a suffix and not `docs/en/` + `docs/es/`

The directory layout is the obvious one, and it is the one that cannot be adopted
incrementally here.

This repository has **the ADRs, the gotchas catalogue, the whole roadmap and the
seven canonical folders** written in Spanish. Measured on 2026-08-21: 666 Markdown
files under `docs/`, 968k words, of which 326 declare `docs_language: es` and three
declare `en`. With over a thousand internal `.md` links between them and
several guards watching those links
([`tests/docs/test_docs_internal_links.py`](../../tests/docs/test_docs_internal_links.py)),
the folder structure
(`tests/integration/test_docs_structure_guardrail.py`) and the guide index
(`tests/docs/test_docs_training_model.py`). Moving a document into `docs/es/`
rewrites every inbound link to it, and until the last one moves the corpus lives
in two layouts at once — which is exactly the half-finished state a bilingual
policy is supposed to prevent.

The sidecar has the property the directory split lacks: **translating a document
never breaks a link.**

```text
before:  docs/03-guides/foo.md            (Spanish, N inbound links)
step 1:  git mv foo.md foo.es.md          (Spanish moves aside)
step 2:  write foo.md in English          (canonical takes the bare name)
after:   the N inbound links still resolve — and now land on the canonical language
```

No link rewrite, ever, in either direction. The bare name is a stable address
whose _content_ migrates from Spanish to English one document at a time, and the
Spanish reader follows the header cross-link.

There is a second reason, narrower but not negotiable: **the platform-recognised
filenames are fixed.** GitHub renders `README.md`, tooling and the Keep a
Changelog convention look for `CHANGELOG.md`, `LICENSE` has no suffix. A language
suffix on those loses the platform behaviour, so the bare name has to belong to
one language — and the operator's decision makes that language English.

## What is bilingual today, and what is not

**Bilingual now** — every pair is validated by the guard:

- the root `README.md` and `CHANGELOG.md`;
- the published site: [`mkdocs.yml`](../../mkdocs.yml) runs
  `mkdocs-static-i18n` in **`suffix` mode**, which is this convention exactly —
  bare filename for English, `foo.es.md` for Spanish, one site with a language
  selector and not a single document moved;
- `docs/index.md`, the site home page;
- the architecture diagrams of `docs/01-overview/`
  ([`03-diagrams.md`](../01-overview/03-diagrams.md)). They landed the same day as
  this policy, in another lane, named `03-diagrams.en.md`; they were realigned to
  the bare name before the commit, so the deviation inventory is empty — the
  mechanism stays, the entry does not.
- their six Mermaid diagrams are themselves guarded against the code by
  [`tests/docs/test_diagram_guards.py`](../../tests/docs/test_diagram_guards.py),
  which also refuses a pair whose two halves stop drawing the same node ids —
  the bilingual rule applied to a drawing rather than to prose.
- this policy.

**Not bilingual in this wave**, and deliberately so:

- the ADRs of `05-architecture-decisions/` (160 on 2026-08-21);
- the gotchas of `03-guides/gotchas/` (108 on the same date);
- the roadmap of `docs/roadmap/`;
- the rest of the seven canonical folders.

They stay Spanish-only at their canonical path, declared by `docs_language: es`.
That is a **backlog with a shape**, not a gap: each of those documents becomes
bilingual by the two-step move above, on its own, without coordinating with any
other.

## Adding a new document

1. Write the English version at the bare name (`foo.md`), `docs_language: en`.
2. Write the Spanish version at `foo.es.md`, `docs_language: es`.
3. Put the cross-link in both headers, in the first lines of the document.
4. If the document is a guide, list **both** halves in
   [`README.md`](./README.md) — the index guard requires every `.md` in the
   folder to appear there.

A document that is going to stay Spanish-only for now is not a violation: write
it at the bare name with `docs_language: es` and no sidecar. Only root-level
documents are required to be bilingual, because those are the ones a stranger
reads first.

## Translating a document that already exists

```bash
git mv docs/03-guides/foo.md docs/03-guides/foo.es.md
# then write the English canonical at docs/03-guides/foo.md
```

Add the cross-link to both headers and flip the moved file's frontmatter to
`docs_language: es` if it did not say so already. Do not touch the inbound links:
they point at the bare name, which is still there.

## The guard

[`tests/docs/test_bilingual_docs.py`](../../tests/docs/test_bilingual_docs.py)
enforces the parts of this policy a human forgets:

- every root-level Markdown document is bilingual, except the working files
  named in the exemption list (with their reason written in the test);
- no orphan translation: an `.es.md` whose canonical half does not exist;
- no second naming convention: an `.en.md` file is a misnamed canonical, not an
  alternative, and only the declared deviations of 2026-08-21 are tolerated;
- both halves cross-link each other in the first lines of the body — except where
  the site's own language selector is the switch, which is declared, reasoned, and
  only valid while the document itself tells the reader the selector is there;
- both halves have the same heading structure, so a translation cannot silently
  drop a section;
- internal `.md` links of the root-level bilingual documents resolve — the older
  link guard only walks `docs/`;
- both frozen inventories fail on dead entries, so a deviation that gets fixed
  cannot stay declared;
- and a non-vacuity assertion, because a discovery that finds nothing would pass
  green forever (see
  [verificar-antes-de-implementar.md](./verificar-antes-de-implementar.md) §4).

Every one of those was verified failing before being trusted: nine mutations —
delete a Spanish half, add a section to one half only, break a cross-link, add an
`.en.md`, break an internal link, put a dead entry in each of the two inventories,
and drop the root glob from each of the two markdown gates — each turned exactly
the intended test red and nothing else.

The set of bilingual documents is **discovered**, not hand-listed: the day
someone adds `foo.es.md`, the pair is validated from that commit on. What is
hand-listed is the _exemption_ — the root files that stay monolingual — so the
list can only shrink by accident, never grow by accident.

## This is about documentation, not about the product

Principle 12 of `CLAUDE.md` («supported languages: ES + EN only») is about what
the **product** speaks: the panel's i18n dictionaries, the agent persona, the
generated docs of a tenant's project. That rule is unchanged, and so is the rule
in [`../README.md`](../README.md) that a _generated project's_ `/docs/` sticks to
the single language its project declares.

This policy is about the **platform repository's own documentation**, which is
read by contributors and by whoever finds the public repo. That one is bilingual.

## Related

- [`../README.md`](../README.md) — canonical `/docs` structure and format rules.
- [`../context/conventions.md`](../context/conventions.md) — code, commit and
  Markdown conventions.
- [`verificar-antes-de-implementar.md`](./verificar-antes-de-implementar.md) —
  why a guard that cannot fail is not a guard.
