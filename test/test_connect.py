# eventnxt-backend: test/test_connect.py  (verification harness)
#
# Stripe Connect slice 1 — the org-scoped payout account:
#   1. Status with no account: connected=false everywhere.
#   2. First /connect creates ONE Express account, saves it, returns the
#      onboarding URL; the row starts with all flags false.
#   3. Second /connect reuses the saved account (no second
#      Account.create) and mints a FRESH link — resume-onboarding is the
#      same button.
#   4. Org scoping: a DIFFERENT event under the same org sees the same
#      connected account.
#   5. account.updated webhook flips the three flags; replaying the same
#      Stripe event id is a clean already_processed no-op (idempotency
#      via stripe_webhook_events' unique constraint).
#   6. account.updated for an acct id we don't know: recorded, no_action.
#   7. manage-link: 400 before details_submitted, a Stripe login URL
#      after.
#   8. Fail closed: with STRIPE_CONNECT_WEBHOOK_SECRET empty, the real
#      verifier refuses everything with 503 — same rule as the platform
#      webhook.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_connect.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.payment_account import PaymentAccount
from app.services import stripe_gateway as gateway
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

EV, EV2, ORG = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
ACCT = "acct_TESTCONNECT1"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Connect", "start_date": "2026-12-24", "end_date": "2026-12-24"}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)

# ---- Stripe stubs: the gateway module is the seam (monkeypatch it, the
# router calls through gateway.*). Counters prove call discipline. ----
calls = {"create": 0, "link": 0, "login": 0}


def fake_create_express_account(organization_id):
    calls["create"] += 1
    return {"id": ACCT}


def fake_create_account_link(stripe_account_id, return_url, refresh_url):
    calls["link"] += 1
    assert stripe_account_id == ACCT
    return {"url": f"https://connect.stripe.com/setup/test/{calls['link']}"}


def fake_create_login_link(stripe_account_id):
    calls["login"] += 1
    return {"url": "https://connect.stripe.com/express/test-login"}


gateway.create_express_account = fake_create_express_account
gateway.create_account_link = fake_create_account_link
gateway.create_login_link = fake_create_login_link

# Webhook: bypass signature verification per test (the REAL verifier's
# fail-closed behavior is proven separately in step 8).
_fake_event = None
_real_construct = gateway.construct_connect_webhook_event
gateway.construct_connect_webhook_event = lambda payload, sig: _fake_event


def webhook(event):
    global _fake_event
    _fake_event = event
    return c.post("/webhooks/stripe-connect", content=b"{}", headers={"stripe-signature": "t"})


print("1) status with no account")
r = c.get(f"/events/{EV}/payments")
check("status 200", r.status_code == 200, r.text)
s = r.json()
check("not connected, all flags false", s == {"connected": False, "details_submitted": False, "charges_enabled": False, "payouts_enabled": False}, str(s))

print("2) first connect creates the account")
r = c.post(f"/events/{EV}/payments/connect")
check("connect 200", r.status_code == 200, r.text)
check("returns an onboarding url", r.json()["url"].startswith("https://connect.stripe.com/setup/"))
check("exactly one Account.create", calls["create"] == 1)
db = SessionLocal()
row = db.query(PaymentAccount).filter(PaymentAccount.organization_id == ORG).first()
check("row saved with the acct id", row is not None and row.stripe_account_id == ACCT)
check("flags start false", row is not None and not (row.charges_enabled or row.payouts_enabled or row.details_submitted))
db.close()
s = c.get(f"/events/{EV}/payments").json()
check("status now connected, flags still false", s["connected"] is True and s["charges_enabled"] is False)

print("3) second connect reuses the account, fresh link")
r = c.post(f"/events/{EV}/payments/connect")
check("still exactly one Account.create", calls["create"] == 1, f"create={calls['create']}")
check("a second, fresh link minted", calls["link"] == 2 and r.json()["url"].endswith("/2"))

print("4) another event, same org, same account")
s = c.get(f"/events/{EV2}/payments").json()
check("second event sees the org's account", s["connected"] is True)

print("5) account.updated flips flags; replay is a no-op")
evt_id = "evt_TEST_ACCT_UPDATED_1"
r = webhook({"id": evt_id, "type": "account.updated", "data": {"object": {"id": ACCT, "charges_enabled": True, "payouts_enabled": True, "details_submitted": True}}})
check("webhook 200 updated", r.status_code == 200 and r.json()["status"] == "updated", r.text)
s = c.get(f"/events/{EV}/payments").json()
check("all three flags now true", s["details_submitted"] and s["charges_enabled"] and s["payouts_enabled"], str(s))
r = webhook({"id": evt_id, "type": "account.updated", "data": {"object": {"id": ACCT, "charges_enabled": False, "payouts_enabled": False, "details_submitted": False}}})
check("replay of same event id: already_processed", r.json()["status"] == "already_processed", r.text)
s = c.get(f"/events/{EV}/payments").json()
check("replay changed nothing", s["charges_enabled"] is True)

print("6) account.updated for an unknown acct")
r = webhook({"id": "evt_TEST_UNKNOWN_ACCT", "type": "account.updated", "data": {"object": {"id": "acct_NOBODY", "charges_enabled": True, "payouts_enabled": True, "details_submitted": True}}})
check("recorded, no_action", r.status_code == 200 and r.json()["status"] == "no_action", r.text)

print("7) manage-link gating")
# Force flags off to test the gate, then restore via a fresh webhook event.
db = SessionLocal()
row = db.query(PaymentAccount).filter(PaymentAccount.organization_id == ORG).first()
row.details_submitted = False
db.commit(); db.close()
r = c.post(f"/events/{EV}/payments/manage-link")
check("400 before onboarding is finished", r.status_code == 400, r.text)
r = webhook({"id": "evt_TEST_ACCT_UPDATED_2", "type": "account.updated", "data": {"object": {"id": ACCT, "charges_enabled": True, "payouts_enabled": True, "details_submitted": True}}})
r = c.post(f"/events/{EV}/payments/manage-link")
check("login link after details_submitted", r.status_code == 200 and "express" in r.json()["url"], r.text)
check("login-link call went to Stripe once", calls["login"] == 1)

print("8) fail closed without the Connect secret")
gateway.construct_connect_webhook_event = _real_construct  # the real verifier
from app.config import settings as cfg
saved = cfg.stripe_connect_webhook_secret
cfg.stripe_connect_webhook_secret = ""
r = c.post("/webhooks/stripe-connect", content=b"{}", headers={"stripe-signature": "t"})
check("503 with no secret configured", r.status_code == 503, r.text)
cfg.stripe_connect_webhook_secret = saved

# ---- cleanup (this suite's rows only) ----
db = SessionLocal()
db.query(PaymentAccount).filter(PaymentAccount.organization_id == ORG).delete()
from app.models.stripe_webhook_event import StripeWebhookEvent
db.query(StripeWebhookEvent).filter(StripeWebhookEvent.stripe_event_id.like("evt_TEST_%")).delete(synchronize_session=False)
db.commit(); db.close()

print()
if failures:
    print(f"FAILED: {len(failures)} — " + ", ".join(failures))
    sys.exit(1)
print("test_connect: all checks passed")