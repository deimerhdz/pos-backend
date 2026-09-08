"""Tipos `Annotated` reutilizables para referencias de imagen (spec 080).

Precedente en el proyecto: `app/core/timezone.py::UtcDatetime`
(`Annotated[datetime, PlainSerializer(...)]`, usado en decenas de esquemas de
respuesta).

- ``AssetRefIn``: en la **entrada** (campo de un esquema de request) normaliza
  a key cualquier URL absoluta del bucket gestionado antes de que el servicio
  la vea — el campo nunca llega "sucio" con dominio (FR-004). Debe aplicarse
  **antes** de la comparación "¿cambió la imagen?" que dispara el borrado del
  objeto anterior (data-model.md §4).
- ``AssetUrl``: en la **salida** (campo de un esquema de response) ensambla la
  URL de visualización a partir de la key almacenada, en el momento de
  serializar — la columna de la base de datos no cambia (FR-006/FR-007).

La regla key <-> URL vive en `app/core/storage.py`; aquí solo se envuelve para
aplicarla declarativamente en los bordes.
"""
from typing import Annotated

from pydantic import AfterValidator, PlainSerializer

from app.core.storage import asset_display_url, normalize_asset_ref

AssetRefIn = Annotated[str | None, AfterValidator(normalize_asset_ref)]

AssetUrl = Annotated[
    str | None,
    PlainSerializer(asset_display_url, return_type=str | None),
]
