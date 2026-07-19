from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    smtp_host: str = Field(..., env="SMTP_HOST")
    smtp_port: int = Field(..., env="SMTP_PORT")
    smtp_user: str = Field(..., env="SMTP_USER")
    smtp_password: str = Field(..., env="SMTP_PASSWORD")
    frontend_url: str = Field(..., env="FRONTEND_URL")
    database_url: str = Field(..., env="DATABASE_URL")
    super_admin_email: str = Field(..., env="SUPER_ADMIN_EMAIL")
    allowed_origins: list[str] = ""
    portfolio_url: str = Field(..., env="PORTFOLIO_URL")
    github_url: str = Field(..., env="GITHUB_URL")
    linkedin_url: str = Field(..., env="LINKEDIN_URL")
    cookie_domain: str = Field(..., env="COOKIE_DOMAIN")
    cookie_samesite: str = Field(..., env="COOKIE_SAMESITE")
    cookie_secure: bool = Field(..., env="COOKIE_SECURE")
    gemini_api_key: str = Field(..., env="GEMINI_API_KEY")



    @field_validator("allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v
    
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings()