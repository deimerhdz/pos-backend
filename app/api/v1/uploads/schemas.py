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
    key: str = Field(..., description="Key del objeto dentro del bucket.")
    public_url: str = Field(..., description="URL pública final para guardar en image_url.")
    expires_in: int = Field(..., description="Segundos de validez de upload_url.")
