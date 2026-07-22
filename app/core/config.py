from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL:str = Field(...,env="DATABASE_URL")
    JWT_SECRET:str = Field(...,env="JWT_SECRET")
    JWT_ALGORITHM:str = Field(default="HS256",env="JWT_ALGORITHM")
    ACCESS_TOKEN_EXPIRY:int = Field(default=60*24,env="ACCESS_TOKEN_EXPIRY")

    # QR / sesión de comensal (flujo público de mesas).
    # Ventana deslizante de la sesión (dining_sessions.expires_at).
    SESSION_TTL_MINUTES:int = Field(default=240,env="SESSION_TTL_MINUTES")
    # Tope absoluto del token de sesión (exp del JWT). La sesión se desliza en DB
    # sin re-emitir token; el JWT muere en este tope. Default 24h.
    SESSION_ABS_MAX_MINUTES:int = Field(default=1440,env="SESSION_ABS_MAX_MINUTES")
    # Secreto dedicado para firmar tokens de QR/sesión. Si es None, el helper
    # cae a JWT_SECRET (permite rotación aislada sin obligar cambio de .env).
    QR_TOKEN_SECRET:Optional[str] = Field(default=None,env="QR_TOKEN_SECRET")

    PROJECT_NAME:str ="pos"
    REDIS_URL:str =  Field(env="REDIS_URL")
    # URL base del servicio de email; el envío hace POST a EMAIL_API_URL + /api/email/send.
    EMAIL_API_URL:str = Field(...,env="EMAIL_API_URL")
    MAIL_FROM_NAME:str = Field(...,env="MAIL_FROM_NAME")
    MAIL_FROM:str = Field(...,env="MAIL_FROM")

    SUPER_ADMIN_NAME:str = Field(env="SUPER_ADMIN_NAME")
    SUPER_ADMIN_EMAIL:str = Field(env="SUPER_ADMIN_EMAIL")
    SUPER_ADMIN_PASSWORD:str = Field(env="SUPER_ADMIN_PASSWORD")

    R2_ACCOUNT_ID:str = Field(...,env="R2_ACCOUNT_ID")
    R2_ACCESS_KEY_ID:str = Field(...,env="R2_ACCESS_KEY_ID")
    R2_SECRET_ACCESS_KEY:str = Field(...,env="R2_SECRET_ACCESS_KEY")
    R2_BUCKET_NAME:str = Field(...,env="R2_BUCKET_NAME")
    R2_ENDPOINT_URL:str = Field(...,env="R2_ENDPOINT_URL")
    R2_PUBLIC_BASE_URL:str = Field(...,env="R2_PUBLIC_BASE_URL")
    R2_PRESIGN_EXPIRE_SECONDS:int = Field(default=300,env="R2_PRESIGN_EXPIRE_SECONDS")

    class Config:
        env_file='.env'
        extra='ignore'  # ignora variables del .env que no son de la app (p.ej. POSTGRES_*)
    

settings = Settings()


broker_url = settings.REDIS_URL
result_backend = settings.REDIS_URL