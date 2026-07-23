from pydantic import BaseModel, ConfigDict, Field


class TenantInfoResponse(BaseModel):
    id: int
    name: str
    host: str
    plan: str
    logo_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class TenantUpdate(BaseModel):
    # URL pública del logo ya subido a R2 (vía POST /uploads/presign folder="logo").
    logo_url: str | None = Field(None, max_length=500)
