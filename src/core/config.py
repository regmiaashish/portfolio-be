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
    #Aws S3 Bucket Credentials
    aws_access_key_id: str = Field(..., env="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str = Field(..., env="AWS_SECRET_ACCESS_KEY")
    aws_region: str = Field(..., env="AWS_REGION")
    s3_bucket_name: str = Field(..., env="S3_BUCKET_NAME")

    #Settings for JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 7
    access_token_cookie_name: str = "access_token"
    refresh_token_cookie_name: str = "refresh_token"



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