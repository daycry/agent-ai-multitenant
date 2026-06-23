"""Córtex del System Owner (ADR 0074, F1).

Subsistema tenant-less sobre BYPASSRLS: el aislamiento es por filtro
``owner_user_id`` explícito en todo SQL (excepción consciente al Principio 1 —
no hay RLS de respaldo). Bloque A = persistencia del hilo (``threads``).
"""

from __future__ import annotations
