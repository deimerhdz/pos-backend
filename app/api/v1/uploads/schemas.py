from typing import Literal

from pydantic import BaseModel, Field


class PresignRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255, examples=["helado.jpg"])
    content_type: str = Field(..., min_length=1, max_length=100, examples=["image/jpeg"])
    # Carpeta destino dentro de la ruta del tenant (whitelist; evita keys arbitrarias).
    # "payment-methods": imagen de código QR de un método de pago (spec 032, FR-004).
    folder: Literal["products", "logo", "payment-methods"] = Field(
        "products", description="Carpeta destino: 'products' (default), 'logo' o 'payment-methods'."
    )


class PresignResponse(BaseModel):
    upload_url: str = Field(..., description="URL PUT firmada; sube el archivo directo a R2.")
    key: str = Field(..., description="Key del objeto dentro del bucket. Es lo que se persiste (spec 080).")
    public_url: str = Field(
        ...,
        description=(
            "URL de visualización lista para usar (contra el dominio de assets). "
            "**No** es lo que se persiste: en base de datos vive la `key` (spec 080). "
            "El servidor normaliza a key cualquier URL absoluta del bucket "
            "gestionado que reciba (FR-004), así que reenviar esta `public_url` "
            "también funciona."
        ),
    )
    expires_in: int = Field(..., description="Segundos de validez de upload_url.")
