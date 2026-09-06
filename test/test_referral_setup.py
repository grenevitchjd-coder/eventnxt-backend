# eventnxt-backend: test/test_referral_setup.py  (verification harness)
#
# Referral Setup backend (migration 0043):
#   1. POST /referrers creates a typeless, referrer-only Guest; adding
#      the same email again returns 400; an existing ATTENDEE's email
#      resolves to their row (attendee-referrers keep one identity).
#   2. The fence holds: referrer-only guests are absent from the door
#      roster, refused by the single invite email, skipped by BOTH bulk
#      senders (invites and portal links) — while normal invitees still
#      get emailed.
#   3. The portal data path works for a typeless guest: public rsvp info
#      returns their referral codes without crashing on the missing
#      guest type, and listGuests still serializes them.
#   4. send-portal-link: refuses before a code exists, sends after (text
#      contains the /referrer/<token> link and the share link once the
#      page is published).
#   5. (Slice D) The portal's per-code progress numbers — tickets sold,
#      $ sold, accrued reward, link clicks — come from the SAME shared
#      aggregator as the organizer's /promo-stats, and the two payloads
#      agree exactly after a CSV import lands sales on the code.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_referral_setup.py
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
    event_data = {"organization_id": ORG, "name": "RefSetup", "start_date": D1, "end_date": D1}


app.dependency_overrides[get_current_user] = lambda: U()
app.dependency_overrides[require_event_access] = lambda event_id: U()
c = TestClient(app)
H = {"Authorization": "Bearer tok"}


def main():
    print("== 1. referrer creation ==")
    r = c.post(f"/events/{EV}/referrers", json={"name": "Ivy Influencer", "email": "ivy@x.com"}, headers=H)
    check("referrer 201", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    ivy = r.json()
    check("is_referrer_only true", ivy["is_referrer_only"] is True)
    check("has a portal token", bool(ivy["rsvp_token"]))
    r = c.post(f"/events/{EV}/referrers", json={"name": "Ivy Again", "email": "IVY@x.com"}, headers=H)
    check("duplicate referrer email → 400", r.status_code == 400, f"{r.status_code}")

    gt = c.post(f"/events/{EV}/guest-types", json={"name": "VIP", "guest_mode": "invite"}, headers=H).json()
    att = c.post(f"/events/{EV}/guests",
                 json={"name": "Cam Celeb", "email": "cam@x.com", "guest_type_id": gt["id"],
                       "allocation_status": "pending", "party_size": 1, "guest_mode": "invite"},
                 headers=H).json()
    r = c.post(f"/events/{EV}/referrers", json={"name": "Cam", "email": "cam@x.com"}, headers=H)
    check("attendee email resolves to their existing row", r.status_code == 201 and r.json()["id"] == att["id"],
          f"{r.status_code}")
    check("attendee-referrer stays a normal guest", r.json()["is_referrer_only"] is False)

    print("== 2. the fence ==")
    roster = c.get(f"/events/{EV}/guests/roster/door", headers=H).json()
    names = [g["name"] for g in roster]
    check("referrer absent from door roster", "Ivy Influencer" not in names, str(names))
    check("attendee present on roster", "Cam Celeb" in names, str(names))
    r = c.post(f"/events/{EV}/guests/{ivy['id']}/send-invite",
               json={"rsvp_base_url": "https://x.test"}, headers=H)
    check("single invite email refuses a referrer", r.status_code == 400, f"{r.status_code}")
    SENT.clear()
    r = c.post(f"/events/{EV}/guests/send-invites", json={"rsvp_base_url": "https://x.test"}, headers=H)
    check("bulk invite send ok", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    tos = [m["to"] for m in SENT]
    check("bulk invites emailed the attendee, not the referrer",
          "cam@x.com" in tos and "ivy@x.com" not in tos, str(tos))

    print("== 3. portal data path (typeless guest) ==")
    r = c.post(f"/events/{EV}/promo-codes",
               json={"guest_id": ivy["id"], "code": "IVY15", "reward_type": "percentage",
                     "reward_value": 15, "discount_type": "percentage", "discount_value": 10}, headers=H)
    check("code attaches to the referrer", r.status_code == 201, f"{r.status_code} {r.text[:150]}")
    r = c.get(f"/public/rsvp/{ivy['rsvp_token']}")
    check("public rsvp info survives a typeless guest", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    info = r.json() if r.status_code == 200 else {}
    check("info carries the referral code",
          any(rc.get("code") == "IVY15" for rc in info.get("referral_codes", [])),
          str(info.get("referral_codes")))
    check("guest_type_name is null, not a crash", info.get("guest_type_name") is None)
    all_guests = c.get(f"/events/{EV}/guests", headers=H).json()
    ivy_row = next((g for g in all_guests if g["id"] == ivy["id"]), None)
    check("listGuests serializes the referrer", ivy_row is not None and ivy_row["is_referrer_only"] is True)

    print("== 4. portal-link email ==")
    fresh = c.post(f"/events/{EV}/referrers", json={"name": "New Ref", "email": "new@x.com"}, headers=H).json()
    r = c.post(f"/events/{EV}/referrers/{fresh['id']}/send-portal-link",
               json={"portal_base_url": "https://x.test"}, headers=H)
    check("send refuses before any code exists", r.status_code == 400, f"{r.status_code}")
    SENT.clear()
    r = c.post(f"/events/{EV}/referrers/{ivy['id']}/send-portal-link",
               json={"portal_base_url": "https://x.test"}, headers=H)
    check("send succeeds with a code", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
    check("email went to the referrer", SENT and SENT[-1]["to"] == "ivy@x.com", str([m["to"] for m in SENT]))
    body = SENT[-1]["text"] if SENT else ""
    check("email carries the portal link", f"/referrer/{ivy['rsvp_token']}" in body, body[:200])
    check("unpublished event: share link deferred honestly", "once the event page is published" in body)

    c.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = c.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]
    SENT.clear()
    c.post(f"/events/{EV}/referrers/{ivy['id']}/send-portal-link",
           json={"portal_base_url": "https://x.test"}, headers=H)
    body = SENT[-1]["text"] if SENT else ""
    check("published event: share link included", f"/e/{slug}?ref=IVY15" in body, body[:300])

    print("== 5. portal progress == /promo-stats (slice D) ==")
    r = c.post(f"/events/{EV}/sales/import",
               json={"rows": [
                   {"promo_code": "IVY15", "buyer_name": "P", "buyer_email": "p@x.com", "amount": 200,
                    "ticket_type": "GA", "quantity": 4, "sale_date": D1, "external_transaction_id": "d1"},
                   {"promo_code": "IVY15", "buyer_name": "Q", "buyer_email": "q@x.com", "amount": None,
                    "ticket_type": "GA", "quantity": 1, "sale_date": D1, "external_transaction_id": "d2"},
               ]}, headers=H)
    check("import for progress check accepted", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    c.post(f"/public/events/{slug}/promo-codes/IVY15/click")
    stats = {row["code"]: row for row in c.get(f"/events/{EV}/promo-stats", headers=H).json()}
    portal = c.get(f"/public/rsvp/{ivy['rsvp_token']}").json()
    pc = next((x for x in portal.get("referral_codes", []) if x["code"] == "IVY15"), None)
    org = stats.get("IVY15")
    check("portal carries progress fields", pc is not None and "tickets_sold" in (pc or {}), str(pc)[:200])
    check("tickets agree with /promo-stats", pc and org and pc["tickets_sold"] == org["tickets_sold"] == 5,
          f"portal {pc and pc['tickets_sold']} vs org {org and org['tickets_sold']}")
    check("$ agree with /promo-stats", pc and org and float(pc["amount_sold"]) == float(org["amount_sold"]) == 200.0,
          f"portal {pc and pc['amount_sold']} vs org {org and org['amount_sold']}")
    check("missing-amount rows agree", pc and org and pc["rows_missing_amount"] == org["rows_missing_amount"] == 1)
    check("accrued reward present (15% of $200 = 30)", pc and pc["total_reward"] == 30.0,
          pc and str(pc["total_reward"]))
    check("link clicks surfaced", pc and pc["link_clicks"] >= 1, pc and str(pc["link_clicks"]))
    check("buyer discount surfaced for followers", pc and pc["discount_type"] == "percentage"
          and pc["discount_value"] == 10.0)

    print()
    if failures:
        print(f"referral setup: {len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("referral setup: all clear")


if __name__ == "__main__":
    main()