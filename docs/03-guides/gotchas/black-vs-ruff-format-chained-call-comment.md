---
title: "black y ruff-format se pelean en bucle por un comentario DENTRO de una llamada encadenada"
area: pre-commit, python
encountered: 2026-07-26
stack: pre-commit (black + ruff-format), SQLAlchemy
---

## Síntoma

`git commit` falla una y otra vez. En cada intento, los dos hooks reformatean el
MISMO fichero y ninguno converge:

```
black....................................................................Failed
- files were modified by this hook
reformatted apps/api-server/src/api_server/routers/evals.py
ruff-format..............................................................Failed
- files were modified by this hook
1 file reformatted
```

Re-hacer `git add` y volver a comitear no ayuda: vuelve a pasar.

## Causa raíz

Los dos formateadores discrepan cuando hay un **comentario en medio de una
cadena de llamadas**. black colapsa la cadena si cabe en una línea; ruff-format
la mantiene partida por el comentario. Cada uno deshace lo del otro:

```python
stmt = (
    select(EvalResult)
    .where(EvalResult.run_id == run.id)
    # Los fallos primero: es lo que se viene a mirar.
    .order_by(EvalResult.overall_score.asc().nullsfirst())
)
```

## Fix

Sacar el comentario **fuera** del paréntesis. Con la cadena limpia, los dos
formateadores coinciden:

```python
# Los fallos primero: es lo que se viene a mirar.
stmt = (
    select(EvalResult)
    .where(EvalResult.run_id == run.id)
    .order_by(EvalResult.overall_score.asc().nullsfirst())
)
```

## Cómo verificar el fix

Correr los dos en orden y comprobar que el segundo no toca nada:

```bash
python -m black -q <fichero> && cp <fichero> /tmp/b1.py \
  && python -m ruff format -q <fichero> && diff -q /tmp/b1.py <fichero>
```

`Files ... are identical` = convergen.
