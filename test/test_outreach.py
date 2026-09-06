# eventnxt-backend: test/test_outreach.py  (verification harness)
#
# Slice E — per-recipient outreach and the LOCKED attribution policy:
#   1. /refer creates contacts, emails each a unique tracked link
#      (ref=CODE&r=token), re-entering an email re-sends the SAME token,
#      and an unpublished event refuses (nothing to link to).
#   2. The click ping with r=<token> stamps clicked_at once.
#   3. Checkout attribution:
#      a. token + NO code -> sale credits the contact's code AND person
#         ("no promo code involved" still pays the sender);
#      b. buyer TYPES a different code -> typed code wins, contact
#         stamp dropped;
#      c. last-click-wins is the browser's job (it sends whichever
#         token it remembered last) — the backend honors what arrives.
#   4. Device-switch email fallback at paid time: no code, no token,
#      buyer email matches exactly ONE contact -> credited (code +
#      person + reward); matches TWO contacts (two referrers invited
#      the same person) -> stays unattributed, deliberately.
#   5. CSV import: codeless row single-matches by email -> credited;
#      the portal payload then shows per-person conversion, and the
#      credited numbers agree with /promo-stats (shared aggregator).
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_outreach.py
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

SENT = []


def _capture(to, subject, text_body, html_body=None, attachments=None):
    SENT.append({"to": to, "subject": subject, "text": text_body})


email_mod.send_email = _capture

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
D1 = "2026-12-24"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class U:
    user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "Outreach", "start_date": D1, "end_date": D1}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}

TT = {"description": None, "max_per_order": 20, "admits": 1, "sales_start": None, "sales_end": None,
      "is_active": True, "sort_order": 0, "valid_date": None}


def buy(slug, tt_id, email, qty=1, promo=None, r=None):
    body = {"terms_accepted": True, "buyer_name": "Buyer", "buyer_email": email,
            "items": [{"ticket_type_id": tt_id, "quantity": qty}]}
    if promo is not None:
        body["promo_code"] = promo
    if r is not None:
        body["referral_contact_token"] = r
    return c.post(f"/public/events/{slug}/checkout", json=body)


def sale_for(email):
    sales = c.get(f"/events/{EV}/sales", headers=H).json()
    return next((s for s in sales if s["buyer_email"] == email), None)


def main():
    print("== setup: free GA type (instant-paid path), two referrers ==")
    pool = c.post(f"/events/{EV}/seating-categories",
                  json={"name": "GA", "capacity": 100, "sales_grain": "ga", "row_label": None}, headers=H).json()
    tt = c.post(f"/events/{EV}/ticket-types",
                json={**TT, "name": "GA", "price_cents": 0, "quantity": 100,
                      "seating_category_id": pool["id"]}, headers=H).json()
    sarah = c.post(f"/events/{EV}/referrers", json={"name": "Sarah", "email": "sarah@x.com"}, headers=H).json()
    ben = c.post(f"/events/{EV}/referrers", json={"name": "Ben", "email": "ben@x.com"}, headers=H).json()
    sc = c.post(f"/events/{EV}/promo-codes",
                json={"guest_id": sarah["id"], "code": "SARAH10", "reward_type": "flat_amount",
                      "reward_value": 2}, headers=H).json()
    bc = c.post(f"/events/{EV}/promo-codes",
                json={"guest_id": ben["id"], "code": "BEN10", "reward_type": "flat_amount",
                      "reward_value": 3}, headers=H).json()

    print("== 0. Outreach Policy gate (0051): accept first, stamped once ==")
    SENT.clear()
    r = c.post(f"/public/rsvp/{sarah['rsvp_token']}/refer",
               json={"promo_code_id": sc["id"], "contacts": [{"name": "Jane", "email": "jane@x.com"}]})
    check("refer without policy acceptance -> 400, names the policy",
          r.status_code == 400 and "Outreach Policy" in r.json()["detail"], r.text[:120])
    check("nothing sent on the refusal", len(SENT) == 0)

    print("== 1. /refer: tracked emails, idempotent tokens, publish required ==")
    r = c.post(f"/public/rsvp/{sarah['rsvp_token']}/refer",
               json={"promo_code_id": sc["id"], "contacts": [{"name": "Jane", "email": "jane@x.com"}],
                     "outreach_terms_accepted": True})
    check("unpublished event refuses refer (policy accepted first)", r.status_code == 400, f"{r.status_code}")
    info0 = c.get(f"/public/rsvp/{sarah['rsvp_token']}").json()
    check("acceptance stamped once, survives the publish refusal",
          info0.get("outreach_terms_accepted_at") is not None, info0.get("outreach_terms_accepted_at"))
    c.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = c.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]

    SENT.clear()
    r = c.post(f"/public/rsvp/{sarah['rsvp_token']}/refer",
               json={"promo_code_id": sc["id"],
                     "contacts": [{"name": "Jane", "email": "jane@x.com"}, {"name": "Mike", "email": "mike@x.com"}]})
    check("refer 200", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    check("two invite emails sent", len(SENT) == 2 and {m["to"] for m in SENT} == {"jane@x.com", "mike@x.com"},
          str([m["to"] for m in SENT]))
    jane_link = next((ln for ln in SENT[0]["text"].split() if "?ref=SARAH10&r=" in ln), "")
    check("link is per-recipient tracked", "&r=" in jane_link, SENT[0]["text"][:200])
    jane_token = jane_link.split("&r=")[-1]
    info = r.json()
    scode = next(x for x in info["referral_codes"] if x["code"] == "SARAH10")
    check("portal shows both contacts, unclicked",
          len(scode["contacts"]) == 2 and all(not ct["clicked"] for ct in scode["contacts"]),
          str(scode["contacts"]))
    SENT.clear()
    c.post(f"/public/rsvp/{sarah['rsvp_token']}/refer",
           json={"promo_code_id": sc["id"], "contacts": [{"name": "Jane", "email": "jane@x.com"}]})
    resent = next((ln for ln in SENT[0]["text"].split() if "&r=" in ln), "")
    check("re-refer reuses the SAME token", resent.split("&r=")[-1] == jane_token,
          f"{resent.split('&r=')[-1]} vs {jane_token}")
    # Ben invites the same Jane — allowed; this creates the ambiguity case 4 needs.
    c.post(f"/public/rsvp/{ben['rsvp_token']}/refer",
           json={"promo_code_id": bc["id"], "contacts": [{"name": "Jane", "email": "jane@x.com"}],
                 "outreach_terms_accepted": True})
    # And Sarah alone invites Solo.
    c.post(f"/public/rsvp/{sarah['rsvp_token']}/refer",
           json={"promo_code_id": sc["id"], "contacts": [{"name": "Solo", "email": "solo@x.com"}]})

    print("== 1b. custom subject/message (2026-09-05 adjust) ==")
    SENT.clear()
    r = c.post(f"/public/rsvp/{sarah['rsvp_token']}/refer",
               json={"promo_code_id": sc["id"], "subject": "Come with me to this!",
                     "message": "Hey — I'll be there Friday, you HAVE to come. Use my link below.",
                     "contacts": [{"name": "Pat", "email": "pat@x.com"}]})
    check("custom refer 200", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    m = SENT[-1]
    check("custom subject used", m["subject"] == "Come with me to this!", m["subject"])
    check("custom message in the body", "you HAVE to come" in m["text"], m["text"][:200])
    check("tracked link still appended", "?ref=SARAH10&r=" in m["text"])
    check("on-behalf-of footer kept", "on behalf of Sarah" in m["text"], m["text"][-120:])
    SENT.clear()
    c.post(f"/public/rsvp/{sarah['rsvp_token']}/refer",
           json={"promo_code_id": sc["id"], "contacts": [{"name": "Pat", "email": "pat2@x.com"}]})
    check("default subject when omitted", SENT[-1]["subject"].startswith("Sarah invited you"),
          SENT[-1]["subject"])
    r = c.post(f"/public/rsvp/{sarah['rsvp_token']}/refer",
               json={"promo_code_id": sc["id"], "message": "x" * 2001,
                     "contacts": [{"name": "Pat", "email": "pat3@x.com"}]})
    check("over-cap message rejected", r.status_code == 422, f"{r.status_code}")
    portal = c.get(f"/public/rsvp/{sarah['rsvp_token']}").json()
    check("message draft exposed for prefill",
          "referral_message_draft" in portal["referral_codes"][0])

    print("== 2. click stamping ==")
    r = c.post(f"/public/events/{slug}/promo-codes/SARAH10/click?r={jane_token}")
    check("click ping accepts the token", r.status_code == 204, f"{r.status_code}")
    info = c.get(f"/public/rsvp/{sarah['rsvp_token']}").json()
    scode = next(x for x in info["referral_codes"] if x["code"] == "SARAH10")
    jane_ct = next(ct for ct in scode["contacts"] if ct["email"] == "jane@x.com")
    check("Jane shows clicked on the portal", jane_ct["clicked"] is True)

    print("== 3. checkout attribution ==")
    r = buy(slug, tt["id"], "jane@x.com", qty=2, r=jane_token)  # token, NO code
    check("token-only checkout paid", r.status_code == 200 and r.json().get("status") == "paid",
          f"{r.status_code} {r.text[:150]}")
    js = sale_for("jane@x.com")
    check("sale credited to SARAH10 without a typed code", js and js["promo_code_id"] == sc["id"],
          str(js and js["promo_code_id"]))
    check("reward accrued ($2 x 2)", js and float(js["computed_reward"]) == 4.0, js and str(js["computed_reward"]))
    r = buy(slug, tt["id"], "typed@x.com", qty=1, promo="BEN10", r=jane_token)  # typed code beats token
    check("typed-different-code checkout paid", r.status_code == 200, f"{r.status_code}")
    ts = sale_for("typed@x.com")
    check("typed code wins the sale", ts and ts["promo_code_id"] == bc["id"], str(ts and ts["promo_code_id"]))
    check("contact stamp dropped when codes disagree", ts and ts["referral_contact_id"] is None,
          str(ts and ts["referral_contact_id"]))

    print("== 4. device-switch email fallback ==")
    r = buy(slug, tt["id"], "solo@x.com", qty=1)  # no code, no token — Solo invited by ONE referrer
    check("fallback checkout paid", r.status_code == 200, f"{r.status_code}")
    ss = sale_for("solo@x.com")
    check("single-match email credits Sarah", ss and ss["promo_code_id"] == sc["id"],
          str(ss and ss["promo_code_id"]))
    check("fallback stamps the person too", ss and ss["referral_contact_id"] is not None)
    r = buy(slug, tt["id"], "JANE@x.com", qty=1)  # Jane invited by BOTH — ambiguous, stays unattributed
    check("double-invite checkout paid", r.status_code == 200, f"{r.status_code}")
    sales = [s for s in c.get(f"/events/{EV}/sales", headers=H).json() if s["buyer_email"].lower() == "jane@x.com"]
    ambiguous = [s for s in sales if float(s.get("amount") or 0) == 0 and s["quantity"] == 1]
    check("ambiguous double-invite stays unattributed",
          any(s["promo_code_id"] is None for s in ambiguous), str([(s["quantity"], s["promo_code_id"]) for s in sales]))

    print("== 5. CSV email-match + portal conversion == /promo-stats ==")
    r = c.post(f"/events/{EV}/sales/import",
               json={"rows": [{"promo_code": None, "buyer_name": "Solo", "buyer_email": "solo@x.com",
                               "amount": 50, "ticket_type": "GA", "quantity": 2, "sale_date": D1,
                               "external_transaction_id": "csv1"}]}, headers=H)
    check("codeless import row accepted", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    imported = next((s for s in c.get(f"/events/{EV}/sales", headers=H).json()
                     if s["external_transaction_id"] == "csv1"), None)
    check("imported row credited by email match", imported and imported["promo_code_id"] == sc["id"],
          str(imported and imported["promo_code_id"]))
    info = c.get(f"/public/rsvp/{sarah['rsvp_token']}").json()
    scode = next(x for x in info["referral_codes"] if x["code"] == "SARAH10")
    solo_ct = next(ct for ct in scode["contacts"] if ct["email"] == "solo@x.com")
    check("portal shows Solo bought 3 (native 1 + imported 2)", solo_ct["tickets_bought"] == 3,
          str(solo_ct))
    stats = {row["code"]: row for row in c.get(f"/events/{EV}/promo-stats", headers=H).json()}
    check("portal code totals agree with /promo-stats",
          scode["tickets_sold"] == stats["SARAH10"]["tickets_sold"],
          f"{scode['tickets_sold']} vs {stats['SARAH10']['tickets_sold']}")

    print("== 6. payout agreement in the portal payload ==")
    check("reward terms present ($2 flat)", scode["reward_type"] == "flat_amount"
          and scode["reward_value"] == 2.0, f"{scode['reward_type']} {scode.get('reward_value')}")
    c.post(f"/events/{EV}/bonus-tiers", json={"tickets_required": 20, "bonus_value": 50}, headers=H)
    scode2 = next(x for x in c.get(f"/public/rsvp/{sarah['rsvp_token']}").json()["referral_codes"]
                  if x["code"] == "SARAH10")
    check("effective bonus tiers ride along",
          any(t["tickets_required"] == 20 and t["bonus_value"] == 50.0 for t in scode2["bonus_tiers"]),
          str(scode2["bonus_tiers"]))

    print()
    if failures:
        print(f"outreach: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("outreach: all clear")


if __name__ == "__main__":
    main()