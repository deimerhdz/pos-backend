from pydantic import Field
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL:str = Field(...,env="DATABASE_URL")
    JWT_SECRET:str = Field(...,env="JWT_SECRET")
    JWT_ALGORITHM:str = Field(default="HS256",env="JWT_ALGORITHM")
    ACCESS_TOKEN_EXPIRY:int = Field(default=60*24,env="ACCESS_TOKEN_EXPIRY")
    PROJECT_NAME:str ="pos"
    REDIS_URL:str =  Field(default="redis://localhost:6379/0",env="REDIS_URL")
    RESEND_API_KEY:str = Field(...,env="RESEND_API_KEY")
    MAIL_FROM_NAME:str = Field(...,env="MAIL_FROM_NAME")
    MAIL_FROM:str = Field(...,env="MAIL_FROM")

    SUPER_ADMIN_NAME:str = Field(default="Super Admin",env="SUPER_ADMIN_NAME")
    SUPER_ADMIN_EMAIL:str = Field(default="admin@admin.com",env="SUPER_ADMIN_EMAIL")
    SUPER_ADMIN_PASSWORD:str = Field(default="Admin1234!",env="SUPER_ADMIN_PASSWORD")

    class Config:
        env_file='.env'
        extra='ignore'  # ignora variables del .env que no son de la app (p.ej. POSTGRES_*)
    

settings = Settings()


broker_url = settings.REDIS_URL
result_backend = settings.REDIS_URL