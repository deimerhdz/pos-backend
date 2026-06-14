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
    
    class Config:
        env_file='.env'
    

settings = Settings()


broker_url = settings.REDIS_URL
result_backend = settings.REDIS_URL