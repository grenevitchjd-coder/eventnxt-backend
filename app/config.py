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

    # Email (generic SMTP — provider-agnostic on purpose). Any provider's
    # SMTP relay works: Resend, SendGrid, SES, Postmark, etc. Switching
    # providers is a config-var change, never a code change. Port 587 with
    # STARTTLS is the default because Heroku blocks port 25.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = ""  # e.g. tickets@events360.app — must be a verified sender at the provider

    # Stripe. Test keys (sk_test_...) until real sales day, then a
    # deliberate one-time swap to live keys. The webhook secret is
    # generated when the webhook endpoint is registered with Stripe —
    # empty until then, and the webhook route rejects everything while
    # it's empty (fail closed, never open).
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # Signing secret for the SECOND webhook endpoint — the one registered
    # with "Events from: Connected accounts" (account.updated). Separate
    # endpoint, separate secret, same fail-closed rule: empty means the
    # /webhooks/stripe-connect route rejects everything.
    stripe_connect_webhook_secret: str = ""
    # Enforcement switch for Connect (slice 2). False (default): an org
    # with no connected/enabled payout account still charges into the
    # PLATFORM account — today's sandbox behavior, unchanged. True: paid
    # checkout refuses (409) for such orgs, so no money can land in the
    # platform account by accident. Flip to true via config var before
    # the live-key swap.
    stripe_require_connected_account: bool = False

    # Permissions-bridge enforcement (services/permissions.py). False
    # (default): a userinfo payload with NO permissions field at all is
    # treated as all-access — the pre-bridge rollout fallback, so
    # deploying this app before Events360 ships its permissions bridge
    # can't lock every staff member out. True: that same missing-field
    # case is DENIED instead. Flip to true once Events360's permissions
    # bridge is confirmed deployed and sending the field for every
    # organization on EventNXT — the same "verify, then flip the switch"
    # pattern as STRIPE_REQUIRE_CONNECTED_ACCOUNT.
    permissions_bridge_required: bool = False

    # EventNXT's platform fee, baked into the ticket's face value: the
    # buyer sees a clean price, the organizer bears the fee. These are
    # config so repricing is a config-var change — and every Order
    # SNAPSHOTS the computed fee at creation, so a later repricing never
    # rewrites an existing order's math.
    platform_fee_percent: float = 3.0
    platform_fee_fixed_cents: int = 75
    # Refund-cost reserve rate (0047) — sized to Stripe's card-processing
    # cost, withheld per destination sale and released post-event on
    # still-paid orders. Config so a Stripe repricing is a config-var
    # change; each order SNAPSHOTS its reserve at creation like the fee.
    reserve_percent: float = 2.9
    reserve_fixed_cents: int = 30

    class Config:
        env_file = ".env"


settings = Settings()