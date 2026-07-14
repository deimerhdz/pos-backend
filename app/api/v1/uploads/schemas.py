from pydantic import BaseModel, Field


class PresignRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255, examples=["helado.jpg"])
    content_type: str = Field(..., min_length=1, max_length=100, examples=["image/jpeg"])


class PresignResponse(BaseModel):
    upload_url: str = Field(..., description="URL PUT firmada; sube el archivo directo a R2.")
    key: str = Field(..., description="Key del objeto dentro del bucket.")
    public_url: str = Field(..., description="URL pública final para guardar en image_url.")
    expires_in: int = Field(..., description="Segundos de validez de upload_url.")
