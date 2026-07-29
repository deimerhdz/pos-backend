from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL:str = Field(...,env="DATABASE_URL")
    JWT_SECRET:str = Field(...,env="JWT_SECRET")
    JWT_ALGORITHM:str = Field(default="HS256",env="JWT_ALGORITHM")
    # Vida del access token, en minutos. Default 24h.
    ACCESS_TOKEN_EXPIRY:int = Field(default=60*24,env="ACCESS_TOKEN_EXPIRY")
    # Vida del refresh token, en minutos. Debe ser mayor que ACCESS_TOKEN_EXPIRY;
    # si no, el refresh muere junto al access y no sirve para renovar. Default 7 días.
    REFRESH_TOKEN_EXPIRY_MINUTES:int = Field(default=60*24*7,env="REFRESH_TOKEN_EXPIRY_MINUTES")

    # QR / sesión de comensal (flujo público de mesas).
    # Ventana deslizante del comensal (session_participants.expires_at).
    SESSION_TTL_MINUTES:int = Field(default=240,env="SESSION_TTL_MINUTES")
    # Tope absoluto del token de sesión (exp del JWT). La sesión se desliza en DB
    # sin re-emitir token; el JWT muere en este tope. Default 24h.
    SESSION_ABS_MAX_MINUTES:int = Field(default=1440,env="SESSION_ABS_MAX_MINUTES")
    # Secreto dedicado para firmar tokens de QR/sesión. Si es None, el helper
    # cae a JWT_SECRET (permite rotación aislada sin obligar cambio de .env).
    QR_TOKEN_SECRET:Optional[str] = Field(default=None,env="QR_TOKEN_SECRET")

    # Sesión de mesa: máximo que puede seguir abierta sin que el staff la cierre.
    # Pasado ese tiempo la cierra el barrido programado, porque si no la mesa
    # queda 'ocupada' para siempre.
    TABLE_SESSION_MAX_HOURS:int = Field(default=6,env="TABLE_SESSION_MAX_HOURS")
    # Inactividad tras la que se libera una mesa **sin ningún pedido**: alguien
    # escaneó y se fue sin pedir. Es mucho más corto que TABLE_SESSION_MAX_HOURS
    # porque el riesgo es el opuesto: soltar una mesa que nadie usó no cuesta nada,
    # dejarla bloqueada media tarde sí. Con pedidos vivos manda el tope de 6h.
    EMPTY_SESSION_TTL_MINUTES:int = Field(default=30,env="EMPTY_SESSION_TTL_MINUTES")
    # Cada cuánto corre ese barrido.
    SESSION_SWEEP_INTERVAL_MINUTES:int = Field(default=15,env="SESSION_SWEEP_INTERVAL_MINUTES")

    # Rate limiting de las rutas públicas del QR (ventana deslizante en Redis).
    RATE_LIMIT_ENABLED:bool = Field(default=True,env="RATE_LIMIT_ENABLED")
    # Peticiones permitidas por IP y por mesa dentro de la ventana.
    RATE_LIMIT_PER_IP:int = Field(default=60,env="RATE_LIMIT_PER_IP")
    RATE_LIMIT_PER_TABLE:int = Field(default=120,env="RATE_LIMIT_PER_TABLE")
    RATE_LIMIT_WINDOW_SECONDS:int = Field(default=60,env="RATE_LIMIT_WINDOW_SECONDS")

    PROJECT_NAME:str ="pos"
    # Ambiente de ejecución: "prod" o "dev". Afecta, p. ej., la URL de login del correo.
    ENVIRONMENT:str = Field(default="dev",env="ENVIRONMENT")
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