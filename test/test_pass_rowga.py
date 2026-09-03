# eventnxt-backend: test/test_pass_rowga.py  (verification harness)
#
# Row + GA all-days passes (the unassigned mirror of the derived pass)
# against a REAL Postgres:
#   1. a sectioned (row) family can grow a pass; public listing carries
#      pass_nights (one section list per night, live remaining) and the
#      pass is NOT seat-picked
#   2. buying with a DIFFERENT section each night: one dated code per
#      night, claims land on each chosen night's section — nightly
#      remaining drops exactly there, honest both directions
#   3. a full section on one night refuses the pass for that pick but
#      allows another section that night; missing picks are refused
#   4. GA family pass: consumes plain nightly counts — pass availability
#      = min over nights; a nightly sale on the thinnest night shrinks
#      the pass; the pass can't oversell the thinnest night, even racing
#      a nightly buyer (real threads)
#   5. a standalone row package converts onto the nightly family
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_pass_rowga.py
import os
import sys
from pathlib import Path

# Runs from the repo root (python3 test/<file>.py) or from inside
# test/ — either way, make the repo root importable for `app`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import threading
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from fastapi.testclient import TestClient

from app.main import app
from app.services.deps import get_current_user
from app.services.event_access import require_event_access

EV = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
D1, D2, D3 = "2026-12-24", "2026-12-25", "2026-12-26"
failures = []


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


class FakeUser:
    user_id = "u-1"
    organization_id = ORG_ID
    name = "Test Owner"
    email = "o@example.com"
    role = "owner"
    raw_token = "tok"
    event_data = {"organization_id": ORG_ID, "name": "Teal Show", "start_date": D1, "end_date": D3}


app.dependency_overrides[get_current_user] = lambda: FakeUser()
app.dependency_overrides[require_event_access] = lambda event_id: FakeUser()
client = TestClient(app)
H = {"Authorization": "Bearer tok"}
TT_BASE = {"description": None, "price_cents": 0, "max_per_order": 10, "admits": 1,
           "sales_start": None, "sales_end": None, "is_active": True, "sort_order": 0}


def buy(slug, items, name="B"):
    return client.post(
        f"/public/events/{slug}/checkout",
        json={"buyer_name": name, "buyer_email": f"{name.lower()}@x.com", "items": items},
    )


def pub_type(slug, tt_id):
    return next(x for x in client.get(f"/public/events/{slug}/ticket-types").json() if x["id"] == tt_id)


def sec_remaining(pt, date, label):
    night = next(n for n in pt["pass_nights"] if n["date"] == date)
    return next(s for s in night["sections"] if s["section_label"] == label)["remaining"]


def main():
    client.patch(f"/events/{EV}/settings", json={"ticket_span": "mixed"}, headers=H)
    client.patch(f"/events/{EV}/profile/refund-policy", json={"refund_policy": "none"}, headers=H)
    slug = client.post(f"/events/{EV}/profile/publish", headers=H).json()["slug"]

    # ---- 1. sectioned family -> pass ----
    pool = client.post(f"/events/{EV}/seating-categories",
                       json={"name": "Row 2 Preferred", "capacity": 1, "sales_grain": "row", "row_label": "Row 2"},
                       headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{pool['id']}/sections",
               json={"sections": [{"section_label": "C", "row_label": "Row 2", "capacity": 3},
                                   {"section_label": "D", "row_label": "Row 2", "capacity": 2}]}, headers=H)
    tt = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "Row 2 Preferred", "quantity": 5,
                           "seating_category_id": pool["id"], "valid_date": D1},
                     headers=H).json()
    client.post(f"/events/{EV}/ticket-types/{tt['id']}/fan-out", headers=H)
    r = client.post(f"/events/{EV}/ticket-types/{tt['id']}/pass",
                    json={"name": "Row 2 Weekend", "price_cents": 0, "quantity": 4, "max_per_order": 4}, headers=H)
    check("row family can grow a pass now", r.status_code == 201, r.text)
    pas = r.json()
    pt = pub_type(slug, pas["id"])
    check("listing: NOT seat-picked, 3 pass_nights with sections",
          pt["assigned_seating"] is False and len(pt["pass_nights"]) == 3
          and all(len(n["sections"]) == 2 for n in pt["pass_nights"]), pt)
    check("night remaining starts at capacity", sec_remaining(pt, D2, "D") == 2, pt["pass_nights"])

    # ---- 2. different section every night ----
    def sec_id(pt, date, label):
        night = next(n for n in pt["pass_nights"] if n["date"] == date)
        return next(s for s in night["sections"] if s["section_label"] == label)["id"]

    picks = [sec_id(pt, D1, "C"), sec_id(pt, D2, "D"), sec_id(pt, D3, "D")]
    r = buy(slug, [{"ticket_type_id": pas["id"], "quantity": 1, "zone_section_ids": picks}], name="V")
    check("pass checkout with per-night sections ok", r.status_code == 200, r.text)
    order = client.get(f"/public/orders/{r.json()['order_token']}").json()
    dates = sorted(t["valid_date"] for t in order["tickets"])
    check("one dated code per night", dates == [D1, D2, D3], order["tickets"])
    pt = pub_type(slug, pas["id"])
    check("claims landed night by night (C on night 1, D on nights 2+3)",
          sec_remaining(pt, D1, "C") == 2 and sec_remaining(pt, D1, "D") == 2
          and sec_remaining(pt, D2, "D") == 1 and sec_remaining(pt, D2, "C") == 3
          and sec_remaining(pt, D3, "D") == 1, pt["pass_nights"])
    # ...and the NIGHTLY buyer sees the same truth in their own picker
    n1 = pub_type(slug, tt["id"])
    n1_c = next(s for s in n1["sections"] if s["section_label"] == "C")["remaining"]
    check("nightly picker shows the pass claim", n1_c == 2, n1["sections"])

    # ---- 3. full-section refusal + missing picks ----
    # Fill D on night 2 (cap 2: 1 pass claim + 2 nightly wanted -> only 1 fits)
    types = client.get(f"/events/{EV}/ticket-types", headers=H).json()
    n2 = next(t for t in types if t["valid_date"] == D2 and not t.get("is_pass"))
    n2_pub = pub_type(slug, n2["id"])
    d2_D = next(s for s in n2_pub["sections"] if s["section_label"] == "D")
    r = buy(slug, [{"ticket_type_id": n2["id"], "quantity": 1, "zone_section_id": d2_D["id"]}], name="N")
    check("nightly buyer takes D's last head on night 2", r.status_code == 200, r.text)
    pt = pub_type(slug, pas["id"])
    r = buy(slug, [{"ticket_type_id": pas["id"], "quantity": 1,
                    "zone_section_ids": [sec_id(pt, D1, "C"), sec_id(pt, D2, "D"), sec_id(pt, D3, "C")]}], name="X")
    check("pass refused when a night's section is full", r.status_code == 400 and "Section D" in r.text, r.text)
    r = buy(slug, [{"ticket_type_id": pas["id"], "quantity": 1,
                    "zone_section_ids": [sec_id(pt, D1, "C"), sec_id(pt, D2, "C"), sec_id(pt, D3, "C")]}], name="X")
    check("same buyer succeeds picking C that night instead", r.status_code == 200, r.text)
    r = buy(slug, [{"ticket_type_id": pas["id"], "quantity": 1}], name="Y")
    check("missing per-night picks refused readably", r.status_code == 400 and "each night" in r.text, r.text)

    # ---- 4. GA family pass ----
    ga = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "GA Standing", "quantity": 3, "seating_category_id": None, "valid_date": D1},
                     headers=H).json()
    client.post(f"/events/{EV}/ticket-types/{ga['id']}/fan-out", headers=H)
    r = client.post(f"/events/{EV}/ticket-types/{ga['id']}/pass",
                    json={"name": "GA Weekend", "price_cents": 0, "quantity": 10, "max_per_order": 5}, headers=H)
    check("GA family can grow a pass now", r.status_code == 201, r.text)
    gpass = r.json()
    check("GA pass availability = thinnest night (3), not its own cap (10)",
          pub_type(slug, gpass["id"])["available"] == 3)
    # a nightly sale shrinks the pass
    r = buy(slug, [{"ticket_type_id": ga["id"], "quantity": 2}], name="G")
    check("nightly GA sale ok", r.status_code == 200, r.text)
    check("pass availability follows the thinnest night down to 1",
          pub_type(slug, gpass["id"])["available"] == 1)
    # ...and a pass sale shrinks every night
    r = buy(slug, [{"ticket_type_id": gpass["id"], "quantity": 1}], name="P")
    check("GA pass checkout ok", r.status_code == 200, r.text)
    order = client.get(f"/public/orders/{r.json()['order_token']}").json()
    check("GA pass mints one dated code per night",
          sorted(t["valid_date"] for t in order["tickets"]) == [D1, D2, D3], order["tickets"])
    types = client.get(f"/events/{EV}/ticket-types", headers=H).json()
    ga_d2 = next(t for t in types if t["valid_date"] == D2 and "GA" in t["name"] and not t.get("is_pass"))
    check("each GA night shows the pass head consumed (3 - 1 = 2 avail on night 2)",
          ga_d2["available"] == 2, ga_d2)
    check("GA pass now sold out via night 1 (2 nightly + 1 pass = 3)",
          pub_type(slug, gpass["id"])["available"] == 0)
    # race: last GA head on night 1 — pass buyer vs nightly buyer, one winner
    r = buy(slug, [{"ticket_type_id": ga["id"], "quantity": 1}], name="R0")
    check("night 1 truly full for setup", r.status_code == 400, r.text)
    ga3 = client.post(f"/events/{EV}/ticket-types",
                      json={**TT_BASE, "name": "GA Lawn", "quantity": 1, "seating_category_id": None, "valid_date": D1},
                      headers=H).json()
    client.post(f"/events/{EV}/ticket-types/{ga3['id']}/fan-out", headers=H)
    lpass = client.post(f"/events/{EV}/ticket-types/{ga3['id']}/pass",
                        json={"name": "Lawn Weekend", "price_cents": 0, "quantity": 5, "max_per_order": 5}, headers=H).json()
    results = []
    def hit(items, nm):
        results.append(buy(slug, items, name=nm).status_code)
    t1 = threading.Thread(target=hit, args=([{"ticket_type_id": ga3["id"], "quantity": 1}], "RA"))
    t2 = threading.Thread(target=hit, args=([{"ticket_type_id": lpass["id"], "quantity": 1}], "RB"))
    t1.start(); t2.start(); t1.join(); t2.join()
    check("race for the last GA head: exactly one winner", sorted(results) == [200, 400], results)

    # ---- 5. standalone row package converts ----
    sp_pool = client.post(f"/events/{EV}/seating-categories",
                          json={"name": "Row 3 Package", "capacity": 1, "sales_grain": "row", "row_label": "Row 3"},
                          headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{sp_pool['id']}/sections",
               json={"sections": [{"section_label": "C", "row_label": "Row 3", "capacity": 4}]}, headers=H)
    sp = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "Row 3 Weekend Package", "quantity": 4,
                           "seating_category_id": sp_pool["id"], "valid_date": None},
                     headers=H).json()
    n3_pool = client.post(f"/events/{EV}/seating-categories",
                          json={"name": "Row 3 Nightly", "capacity": 1, "sales_grain": "row", "row_label": "Row 3"},
                          headers=H).json()
    client.put(f"/events/{EV}/seating-categories/{n3_pool['id']}/sections",
               json={"sections": [{"section_label": "C", "row_label": "Row 3", "capacity": 4}]}, headers=H)
    n3 = client.post(f"/events/{EV}/ticket-types",
                     json={**TT_BASE, "name": "Row 3 Nightly", "quantity": 4,
                           "seating_category_id": n3_pool["id"], "valid_date": D1},
                     headers=H).json()
    client.post(f"/events/{EV}/ticket-types/{n3['id']}/fan-out", headers=H)
    r = client.post(f"/events/{EV}/ticket-types/{sp['id']}/convert-to-pass",
                    json={"template_type_id": n3["id"]}, headers=H)
    check("standalone ROW package converts onto the nightly family", r.status_code == 200 and r.json()["is_pass"], r.text)
    check("converted row pass gets pass_nights", len(pub_type(slug, sp["id"])["pass_nights"]) == 3)

    print()
    if failures:
        print(f"row/GA passes: {len(failures)} FAILED — " + ", ".join(failures))
        sys.exit(1)
    print("row/GA passes: all clear")


if __name__ == "__main__":
    main()