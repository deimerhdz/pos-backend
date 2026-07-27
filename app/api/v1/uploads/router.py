"""Presigned uploads a Cloudflare R2 (S3-compatible). Solo imágenes de producto:
el cliente pide una URL firmada, sube el archivo directo a R2, y luego guarda la
`public_url` resultante en `Product.image_url` vía el endpoint de productos ya
existente (PATCH /products/{id})."""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.db import get_tenant
from app.core.dependencies import require_tenant_admin
from app.core.models import Tenant, User
from app.core.storage import (
    CONTENT_TYPE_EXTENSIONS,
    build_object_key,
    generate_presigned_put_url,
    public_url_for,
)
from app.api.v1.uploads.schemas import PresignRequest, PresignResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post(
    "/presign",
    response_model=PresignResponse,
    summary="Genera una URL firmada para subir una imagen de producto a R2",
    description=(
        "Devuelve una URL PUT firmada (expira en unos minutos) para que el "
        "cliente suba el archivo directo a R2, y la URL pública final que luego "
        "se guarda en `image_url` vía PATCH /products/{id}."
    ),
    responses={
        401: {"description": "No autenticado o token inválido."},
        403: {"description": "El usuario no es administrador del tenant."},
        422: {"description": "content_type no soportado."},
    },
)
def presign_upload(
    body: PresignRequest,
    tenant: Tenant = Depends(get_tenant),
    _: User = Depends(require_tenant_admin),
):
    extension = CONTENT_TYPE_EXTENSIONS.get(body.content_type)
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"content_type no soportado: {body.content_type}",
        )

    # La key nunca usa el filename del cliente (evita path traversal / keys raras);
    # solo se loguea el nombre original para trazabilidad.
    logger.info(
        "Presign de subida solicitado: tenant=%s folder=%s filename=%s",
        tenant.schema, body.folder, body.filename,
    )

    key = build_object_key(tenant.schema, body.folder, extension)
    upload_url = generate_presigned_put_url(key, body.content_type)

    return PresignResponse(
        upload_url=upload_url,
        key=key,
        public_url=public_url_for(key),
        expires_in=settings.R2_PRESIGN_EXPIRE_SECONDS,
    )
