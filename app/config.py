from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Central app configuration, loaded from environment variables.
    """

    database_url: str = "postgresql://localhost/eventnxt_dev"

    # Events360 connection — EventNXT has no auth system of its own, it's
    # entirely an OAuth2 client of Events360 ("Sign in with Events360").
    events360_api_url: str = "http://localhost:8000"  # Events360 BACKEND
    events360_frontend_url: str = "http://localhost:5173"  # Events360 FRONTEND (the authorize page)
    oauth_client_id: str = "eventnxt"
    oauth_client_secret: str = ""  # set via OAUTH_CLIENT_SECRET, from Events360's seed_oauth_client output
    eventnxt_backend_url: str = "http://localhost:9000"  # this app's own URL, for building the callback
    eventnxt_frontend_url: str = "http://localhost:5174"  # this app's OWN frontend

    # CORS: comma-separated list of allowed frontend origins.
    cors_allowed_origins: str = "http://localhost:5174,http://localhost:3001"

    # Cloudflare R2 (S3-compatible object storage) — for real file uploads
    # (event banner photos). Set these via Heroku config vars once an R2
    # bucket exists; the app works fine without them until upload is
    # actually attempted.
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_public_url_base: str = ""  # e.g. https://pub-xxxx.r2.dev or a custom domain

    class Config:
        env_file = ".env"


settings = Settings()