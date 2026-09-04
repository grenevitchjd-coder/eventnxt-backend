# eventnxt-backend: test/test_fashionxt_guests.py  (verification harness)
#
# Joshua's four FashioNXT guest scenarios, encoded verbatim, against a
# REAL Postgres — plus the 0039 machinery they ride on:
#   1. CELEBRITY — type: invite, day_scope 'all', 2 tickets, hold now.
#      Added bare (no day rows anywhere) → grants derive from CURRENT
#      event days; RSVP yes mints 2 codes per night; when the event
#      grows a day, the derived offer follows (self-healing).
#   2. INFLUENCER — type default hold timing 'on_confirm' inherited at
#      add; explicit Fri-only grant on the guest (the type carries NO
#      dates — one type serves Thu-only and Fri-only offers).
#   3. VOLUNTEER — choose-within-caps as DATA: caps Thu 2 / Fri 2 with
#      a total of 2 (from the type's 'choose' scope). RSVP reports the
#      chooser; 2+2 refused, 2 Thu accepted, codes match.
#   4. SPONSOR — explicit distribute mode; and the Auto landmine stays
#      dead: a modeless guest holding day grants is an INVITE now, and
#      their grants mint fully (the 2+2→1+1 bug).
#   5. Invite emails: single send stamps link_sent_at; bulk "all
#      unsent" hits invitees only (skips holders + recipients).
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_fashionxt_guests.py
import os
import sys
from pathlib import Path

# Runs from the repo root (python3 test/<file>.py) or from inside
# test/ — either way, make the repo root importable for `app`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

import app.services.email as email_mod
from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
D1, D2, D3 = "2026-12-24", "2026-12-25", "2026-12-26"
D4 = "2026-12-27"
failures = []
outbox = []
email_mod.send_email = lambda **kw: outbox.append(kw)  # capture, never SMTP


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class FakeUser:
    user_id = "u-1"
    organization_id = ORG
    name = "Test Owner"
    email = "o@example.com"
    role = "owner"
    raw_token = "tok"
    event_data = {"organization_id": ORG, "name": "FashioNXT", "start_date": D1, "end_date": D3}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}
G_BASE = {"party_size": 1, "perks": None, "comments": None}


def add_type(name, **kw):
    return client.post(f"/events/{EV}/guest-types",
                       json={"name": name, "ticket_allotment": 0, "perks": None, "comments": None, **kw}, headers=H).json()


def add_guest(name, gt, **kw):
    return client.post(f"/events/{EV}/guests",
                       json={"name": name, "email": f"{name.split()[0].lower()}@x.com", "guest_type_id": gt["id"],
                             "allocation_status": "pending", **G_BASE, **kw}, headers=H).json()


def rsvp_get(g):
    return client.get(f"/public/rsvp/{g['rsvp_token']}").json()


def rsvp_answer(g, body):
    return client.post(f"/public/rsvp/{g['rsvp_token']}/respond", json=body)


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "per_day", "ticketing_mode": "native", "comp_delivery": "rsvp_required"}, headers=H)

    # ---- 1. CELEBRITY ----
    celeb_t = add_type("Celebrity", guest_mode="invite", day_scope="all", default_ticket_count=2, default_hold_timing="now")
    check("type carries shape defaults", celeb_t["day_scope"] == "all" and celeb_t["default_ticket_count"] == 2, celeb_t)
    carey = add_guest("Carey Grant", celeb_t)
    check("hold timing inherited from type", carey["hold_timing"] == "now", carey)
    info = rsvp_get(carey)
    offered = {x["date"]: x["quantity"] for x in (info.get("day_grants") or [])}
    check("bare celebrity offered 2 per CURRENT night (derived, no rows anywhere)",
          offered == {D1: 2, D2: 2, D3: 2}, info)
    check("fixed offer — no chooser", info.get("choose_within_caps") is False, info)
    r = rsvp_answer(carey, {"attending": True})
    check("celebrity RSVP yes", r.status_code == 200, r.text)
    g = client.get(f"/events/{EV}/guests", headers=H).json()
    carey_row = next(x for x in g if x["name"] == "Carey Grant")
    check("6 codes minted (2 × 3 nights)", carey_row["ticket_count"] == 6, carey_row)
    # self-heal: the event grows a night — a NEW bare celebrity's offer follows
    FakeUser.event_data = {**FakeUser.event_data, "end_date": D4}
    client.get(f"/events/{EV}/settings", headers=H)  # settings self-heal on read
    carey2 = add_guest("New Celeb", celeb_t)
    offered2 = {x["date"]: x["quantity"] for x in (rsvp_get(carey2).get("day_grants") or [])}
    check("date shift self-heals: new night appears in the derived offer",
          offered2 == {D1: 2, D2: 2, D3: 2, D4: 2}, offered2)
    FakeUser.event_data = {**FakeUser.event_data, "end_date": D3}
    client.get(f"/events/{EV}/settings", headers=H)

    # ---- 2. INFLUENCER ----
    infl_t = add_type("Influencer", guest_mode="invite", day_scope="single", default_ticket_count=2, default_hold_timing="on_confirm")
    fri_person = add_guest("Fri Influencer", infl_t, ticket_allotment=[{"date": D2, "quantity": 2}])
    thu_person = add_guest("Thu Influencer", infl_t, ticket_allotment=[{"date": D1, "quantity": 2}])
    check("hold timing 'on RSVP yes' inherited from type",
          fri_person["hold_timing"] == "on_confirm" and thu_person["hold_timing"] == "on_confirm")
    check("ONE type serves Thu-only and Fri-only offers",
          {x["date"] for x in fri_person["ticket_allotment"]} == {D2}
          and {x["date"] for x in thu_person["ticket_allotment"]} == {D1})
    r = rsvp_answer(fri_person, {"attending": True})
    check("influencer RSVP yes mints 2 Friday codes", r.status_code == 200 and
          next(x for x in client.get(f"/events/{EV}/guests", headers=H).json() if x["id"] == fri_person["id"])["ticket_count"] == 2, r.text)

    # ---- 3. VOLUNTEER ----
    vol_t = add_type("Volunteer", guest_mode="invite", day_scope="choose", default_ticket_count=2)
    vol = add_guest("Val Volunteer", vol_t, ticket_allotment=[{"date": D1, "quantity": 2}, {"date": D2, "quantity": 2}])
    info = rsvp_get(vol)
    check("chooser reported: 2 to place across Thu/Fri caps",
          info.get("choose_within_caps") is True and info.get("spend_total") == 2
          and sorted(info.get("available_days") or []) == [D1, D2], info)
    r = rsvp_answer(vol, {"attending": True, "day_quantities": {D1: 2, D2: 2}})
    check("2+2 refused (only 2 to place)", r.status_code == 400 and "2 tickets to place" in r.text, r.text)
    r = rsvp_answer(vol, {"attending": True, "day_quantities": {D1: 2}})
    check("2 on Thursday accepted", r.status_code == 200, r.text)
    vol_row = next(x for x in client.get(f"/events/{EV}/guests", headers=H).json() if x["id"] == vol["id"])
    check("2 Thursday codes minted", vol_row["ticket_count"] == 2, vol_row)

    # ---- 4. SPONSOR + the dead landmine ----
    spon_t = add_type("Sponsor", guest_mode="distribute")
    spon = add_guest("SponsorCo", spon_t, allocation_status="confirmed", guest_mode="distribute",
                     ticket_allotment=[{"date": D1, "quantity": 10}, {"date": D2, "quantity": 10}, {"date": D3, "quantity": 5}])
    check("sponsor classifies as distributor (explicitly)", spon["effective_mode"] == "distribute", spon)
    # the landmine: modeless guest + day grants must be an INVITE whose grants mint fully
    plain_t = add_type("Plain")
    mine = add_guest("Modeless Mia", plain_t, allocation_status="confirmed",
                     ticket_allotment=[{"date": D1, "quantity": 2}, {"date": D2, "quantity": 2}])
    check("Auto landmine dead: modeless guest with grants is an invite", mine["effective_mode"] == "invite", mine)
    r = client.post(f"/events/{EV}/guests/{mine['id']}/sync-tickets", json={"resend": False}, headers=H)
    check("...and their grants mint FULLY (2+2 = 4, not 1+1)", r.json().get("ticket_count") == 4, r.text)

    # ---- 5. invite emails ----
    outbox.clear()
    r = client.post(f"/events/{EV}/guests/{thu_person['id']}/send-invite",
                    json={"rsvp_base_url": "https://eventnxt.events360.app"}, headers=H)
    check("single invite emails + stamps", r.status_code == 200 and r.json()["link_sent_at"] and len(outbox) == 1, r.text)
    check("email carries the link + the offer",
          outbox and thu_person["rsvp_token"] in outbox[0]["text_body"] and "2 tickets" in outbox[0]["text_body"], outbox)
    outbox.clear()
    r = client.post(f"/events/{EV}/guests/send-invites", json={"rsvp_base_url": "https://eventnxt.events360.app"}, headers=H)
    body = r.json()
    # unsent invitees at this point: Carey, New Celeb, Fri Influencer, Val, Modeless Mia (thu_person stamped; sponsor skipped)
    check("bulk hits every unsent invitee, skips the sponsor",
          r.status_code == 200 and body["sent"] == 5 and body["failed"] == 0
          and not any("SponsorCo" in (m.get("to") or "") for m in outbox), body)
    r2 = client.post(f"/events/{EV}/guests/send-invites", json={"rsvp_base_url": "https://eventnxt.events360.app"}, headers=H)
    check("bulk is idempotent — nothing left unsent", r2.json()["sent"] == 0, r2.text)

    print()
    if failures:
        print(f"fashionxt guests: {len(failures)} FAILED — " + ", ".join(failures))
        sys.exit(1)
    print("fashionxt guests: all clear")


if __name__ == "__main__":
    main()