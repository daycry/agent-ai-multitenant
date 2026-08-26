"""Cargar los compose del repo sin atragantarse con sus etiquetas propias.

Vivía dentro de `test_compose_stacks_are_launchable.py`. Se saca aquí cuando
`test_infra_images_are_scanned.py` necesita lo mismo: una segunda copia de un
loader que se equivoca en silencio (devolviendo `{}` en vez de fallar, por
ejemplo) daría una guarda que pasa en vacío sobre tres de los nueve compose.
"""

from __future__ import annotations

from typing import Any

import yaml


class ComposeLoader(yaml.SafeLoader):
    """`SafeLoader` que no se atraganta con las etiquetas propias de compose.

    Los overlays usan `!reset` y `!override` (compose ≥ 2.24) para sustituir una
    lista en vez de fusionarla — p. ej. `volumes: !reset` en `dev.yml`.
    `yaml.safe_load` a secas revienta con `could not determine a constructor`, y
    ese error se lee como «el fichero está roto» cuando el fichero está bien.
    """


def _ignore_unknown_tag(loader: yaml.Loader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


ComposeLoader.add_multi_constructor("!", _ignore_unknown_tag)
