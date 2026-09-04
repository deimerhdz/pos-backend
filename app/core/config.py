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

    # Zona horaria en la que se evalúa la vigencia de promociones y horarios.
    # Antes todo se evaluaba en UTC, lo que en UTC-5 no solo corría la ventana
    # horaria: también el día de la semana, el día del mes y el corte de
    # `ends_at`. Un "20% los martes" arrancaba el lunes a las 19:00 locales.
    TENANT_TIMEZONE:str = Field(default="America/Bogota",env="TENANT_TIMEZONE")

    # QR / sesión de comensal (flujo público de mesas).
    # Ventana deslizante del comensal (session_participants.expires_at).
    SESSION_TTL_MINUTES:int = Field(default=240,env="SESSION_TTL_MINUTES")
    # Tope absoluto del token de sesión (exp del JWT). La sesión se desliza en DB
    # sin re-emitir token; el JWT muere en este tope. Default 24h.
    SESSION_ABS_MAX_MINUTES:int = Field(default=1440,env="SESSION_ABS_MAX_MINUTES")
    # Secreto dedicado para firmar tokens de QR/sesión. Si es None, el helper
    # cae a JWT_SECRET (permite rotación aislada sin obligar cambio de .env).
    QR_TOKEN_SECRET:Optional[str] = Field(default=None,env="QR_TOKEN_SECRET")

    # Holgura del refresco deslizante: `expires_at` solo se reescribe cuando lleva
    # más de estos minutos sin moverse. Sin esto cada lectura del comensal era un
    # UPDATE+COMMIT (~360/h por sondeo); con 10 min son ~6/h.
    #
    # **Invariante: debe ser MENOR que EMPTY_SESSION_TTL_MINUTES.** El barrido
    # deriva la última actividad como `expires_at - SESSION_TTL_MINUTES`
    # (scheduler.py:64), así que esta holgura es también el error máximo de esa
    # derivación. Si la supera, el barrido cierra mesas **activas** sin pedidos.
    SESSION_TTL_REFRESH_SLACK_MINUTES:int = Field(default=10,env="SESSION_TTL_REFRESH_SLACK_MINUTES")

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

    # Rechazar con 422 las selecciones de opciones que violen min/max_select o que
    # usen un grupo no asignado al producto. Arranca en False porque el catálogo
    # histórico nunca se validó (correr `python -m app.scripts.opciones_fuera_de_grupo`
    # antes de activarlo); mientras está en False solo se loguea un WARNING.
    #
    # **Los grupos que la variante consume por slot de receta se validan siempre**,
    # aun con el flag apagado: ahí la cardinalidad decide cuánto inventario se
    # descuenta, así que tolerarla descuadra el stock.
    STRICT_OPTION_SELECTION:bool = Field(default=False,env="STRICT_OPTION_SELECTION")

    # Rate limiting de las rutas públicas del QR (ventana deslizante en Redis).
    RATE_LIMIT_ENABLED:bool = Field(default=True,env="RATE_LIMIT_ENABLED")
    # Peticiones permitidas por IP y por mesa dentro de la ventana.
    RATE_LIMIT_PER_IP:int = Field(default=60,env="RATE_LIMIT_PER_IP")
    RATE_LIMIT_PER_TABLE:int = Field(default=120,env="RATE_LIMIT_PER_TABLE")
    RATE_LIMIT_WINDOW_SECONDS:int = Field(default=60,env="RATE_LIMIT_WINDOW_SECONDS")

    # Recuperación de contraseña (spec 031): límite de solicitudes por correo
    # ingresado, ventana deslizante genuina sobre Redis (no la ventana fija de
    # RATE_LIMIT_*, ver app/core/rate_limit.py::enforce_sliding_window).
    PASSWORD_RESET_MAX_REQUESTS:int = Field(default=3,env="PASSWORD_RESET_MAX_REQUESTS")
    PASSWORD_RESET_WINDOW_SECONDS:int = Field(default=900,env="PASSWORD_RESET_WINDOW_SECONDS")
    # Vigencia del enlace de un solo uso enviado por correo, en minutos.
    PASSWORD_RESET_TOKEN_EXPIRY_MINUTES:int = Field(default=30,env="PASSWORD_RESET_TOKEN_EXPIRY_MINUTES")

    # ---------------- Tiempo real (SSE + Redis Streams) ----------------
    # Interruptor de pánico: en false el publicador es un no-op y el endpoint
    # responde 503, así los clientes caen al sondeo sin desplegar frontend.
    REALTIME_ENABLED:bool = Field(default=True,env="REALTIME_ENABLED")
    # Retención del stream por tenant (XADD MAXLEN ~). Cubre horas de operación;
    # más allá, el cliente recibe `resync` y recarga por REST.
    REALTIME_STREAM_MAXLEN:int = Field(default=1000,env="REALTIME_STREAM_MAXLEN")
    # Comentario `: ping` para mantener vivo el túnel y detectar el otro extremo
    # muerto. Debe ser MENOR que el proxy_read_timeout de nginx.
    REALTIME_HEARTBEAT_SECONDS:int = Field(default=20,env="REALTIME_HEARTBEAT_SECONDS")
    # Vida máxima de una conexión. Al vencer se cierra limpio y EventSource
    # reconecta revalidando el token: evita sockets inmortales con credenciales
    # viejas, que es el punto débil clásico de los JWT en conexiones largas.
    REALTIME_MAX_CONNECTION_SECONDS:int = Field(default=1800,env="REALTIME_MAX_CONNECTION_SECONDS")
    # `retry:` que se envía al navegador; fija su backoff de reconexión.
    REALTIME_RETRY_MS:int = Field(default=3000,env="REALTIME_RETRY_MS")
    # Cola por suscriptor. Si se llena se descarta al cliente con `resync` en vez
    # de acumular memoria sin techo.
    REALTIME_QUEUE_SIZE:int = Field(default=100,env="REALTIME_QUEUE_SIZE")
    # Tope de eventos a repetir tras un Last-Event-ID. Más que esto es `resync`.
    REALTIME_REPLAY_MAX:int = Field(default=200,env="REALTIME_REPLAY_MAX")
    # Cuánto bloquea cada XREAD del lector por tenant.
    REALTIME_READER_BLOCK_MS:int = Field(default=15000,env="REALTIME_READER_BLOCK_MS")
    # Margen antes de parar el lector de un tenant sin suscriptores; sin esto un
    # F5 del cajero destruye y recrea task + conexión Redis.
    REALTIME_READER_LINGER_SECONDS:int = Field(default=30,env="REALTIME_READER_LINGER_SECONDS")
    # Vida del ticket de un solo uso del staff (se consume con GETDEL).
    REALTIME_TICKET_TTL_SECONDS:int = Field(default=30,env="REALTIME_TICKET_TTL_SECONDS")
    # Conexiones simultáneas por sesión de mesa: una mesa no tiene 50 comensales.
    REALTIME_MAX_CONN_PER_SESSION:int = Field(default=8,env="REALTIME_MAX_CONN_PER_SESSION")
    # Timeout del XADD. Publicar no puede colgar una operación de negocio ya
    # comprometida, así que se acota fuerte y se falla en abierto.
    REALTIME_PUBLISH_TIMEOUT_SECONDS:float = Field(default=0.25,env="REALTIME_PUBLISH_TIMEOUT_SECONDS")

    PROJECT_NAME:str ="pos"
    # Ambiente de ejecución: "prod" o "dev". Afecta, p. ej., la URL de login del correo.
    ENVIRONMENT:str = Field(default="dev",env="ENVIRONMENT")
    # Volcado de cada SQL al log. `None` = deriva del entorno (dev sí, prod no).
    # En producción cada sondeo serializaba ~6 sentencias a texto y las escribía:
    # era el mayor coste por request.
    SQL_ECHO:Optional[bool] = Field(default=None,env="SQL_ECHO")
    # DSN de Sentry (spec 068). `None` = Sentry nunca se inicializa, sin importar el
    # entorno. Cuando está presente, solo se usa si además ENVIRONMENT == "prod"
    # (app/main.py) — nunca el DSN de producción en un entorno no productivo.
    SENTRY_DSN:Optional[str] = Field(default=None,env="SENTRY_DSN")
    # Clave HMAC para el log de auditoría de órdenes (spec 074): transforma de forma
    # no reversible el nombre del comensal y el comprobante de pago antes de
    # enviarlos a Sentry Logs. Opcional aquí a propósito (nunca requerido, a
    # diferencia de JWT_SECRET) para no romper el arranque de toda la app en un
    # entorno que todavía no la tenga configurada; su ausencia se detecta y falla
    # explícitamente en app/core/order_audit.py, no aquí. Dominio de seguridad
    # propio: nunca cae a JWT_SECRET ni a ningún otro secreto existente.
    AUDIT_HASH_SECRET:Optional[str] = Field(default=None,env="AUDIT_HASH_SECRET")
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