import os

# Set before importing anything from src: environment variables take
# precedence over .env, so tests never see real secrets or the real database.
# The DATABASE_URL points at a local placeholder and is never connected to,
# because the repository is replaced with an in-memory fake below.
TEST_ENV = {
    "ENV": "test",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/portfolio_test",
    "SMTP_HOST": "localhost",
    "SMTP_PORT": "1025",
    "SMTP_USER": "test@example.com",
    "SMTP_PASSWORD": "test",
    "SUPER_ADMIN_EMAIL": "admin@example.com",
    "PORTFOLIO_URL": "http://localhost:5173",
    "GITHUB_URL": "https://github.com/example",
    "LINKEDIN_URL": "https://linkedin.com/in/example",
    "ALLOWED_ORIGINS": '["http://localhost:5173"]',
    "COOKIE_DOMAIN": "",
    "COOKIE_SAMESITE": "lax",
    "COOKIE_SECURE": "false",
    "GEMINI_API_KEY": "test",
    "JWT_SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256",
    "JWT_ALGORITHM": "HS256",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES": "60",
    "JWT_REFRESH_TOKEN_EXPIRE_DAYS": "7",
    "ACCESS_TOKEN_COOKIE_NAME": "access_token",
    "REFRESH_TOKEN_COOKIE_NAME": "refresh_token",
    "CONTEXT_MOCK": "true",
}
os.environ.update(TEST_ENV)
