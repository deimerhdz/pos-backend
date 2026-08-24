from pydantic import BaseModel, ConfigDict, Field


class TenantInfoResponse(BaseModel):
    id: int
    name: str
    host: str
    plan: str
    logo_url: str | None = None
    receipt_message: str | None = None
    invoice_prefix: str | None = None
    # Zona horaria IANA del negocio (spec 030). Solo lectura — no se agrega a
    # TenantUpdate, ver contracts/tenant-info-endpoint.md.
    timezone: str

    model_config = ConfigDict(from_attributes=True)


class TenantUpdate(BaseModel):
    # URL pública del logo ya subido a R2 (vía POST /uploads/presign folder="logo").
    logo_url: str | None = Field(None, max_length=500)
    # Mensaje que cierra la factura impresa. Cadena vacía = borrarlo (queda NULL);
    # omitirlo = dejarlo como está.
    receipt_message: str | None = Field(None, max_length=255)
    # Prefijo del consecutivo de facturación. Vaciarlo = numeración sin prefijo.
    invoice_prefix: str | None = Field(None, max_length=20)
