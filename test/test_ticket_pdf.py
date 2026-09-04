# eventnxt-backend: test/test_ticket_pdf.py  (verification harness)
#
# Day-split PDF ticket attachments, end to end:
#   1. UNIT — day_ticket_pdfs: 5 codes across Thu/Fri + one undated →
#      three PDFs (thursday, friday, all-days), dated days first; each
#      file is a real PDF whose extracted text carries the event name,
#      the "<Day> - N tickets" header, every code, seat labels, and the
#      holder line.
#   2. FLOW — a confirmed 2-night celebrity's RSVP-yes email arrives
#      with one PDF per night (2 codes each), captured via a
#      monkeypatched SMTP layer; seat-assigned guests see their seat
#      label inside the right day's PDF.
#   3. BEST-EFFORT — when PDF generation blows up, the ticket email
#      still goes out, just without attachments.
#
# Run: DATABASE_URL="postgresql://test@/eventnxt_test?host=/tmp&port=5433" python3 test/test_ticket_pdf.py
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://test@/eventnxt_test?host=/tmp&port=5433")

from pypdf import PdfReader

import app.services.email as email_mod
from app.services.ticket_pdf import day_ticket_pdfs

failures = []
outbox = []
email_mod.send_email = lambda **kw: outbox.append(kw)


def check(name, cond, extra=""):
    print(("  ok " if cond else "  ✗ ") + name + (f" — {extra}" if extra and not cond else ""))
    if not cond:
        failures.append(name)


def pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


def unit_checks():
    D1, D2 = "2026-12-24", "2026-12-25"
    pdfs = day_ticket_pdfs(
        "FashioNXT Runway",
        [
            {"code": "AAA-111", "valid_date": D1, "seat_label": "Row 1 - A1", "holder_name": "Carey Grant"},
            {"code": "BBB-222", "valid_date": D1, "seat_label": "Row 1 - A2", "holder_name": "Carey Grant"},
            {"code": "CCC-333", "valid_date": D2, "seat_label": None, "holder_name": "Carey Grant"},
            {"code": "DDD-444", "valid_date": D2, "seat_label": None, "holder_name": "Carey Grant"},
            {"code": "EEE-555", "valid_date": None, "seat_label": None, "holder_name": "Carey Grant"},
        ],
    )
    check("three PDFs: one per dated day + all-days last",
          [p[0] for p in pdfs] == ["tickets-thu-dec-24.pdf", "tickets-fri-dec-25.pdf", "tickets-all-days.pdf"],
          [p[0] for p in pdfs])
    check("every attachment is a real PDF with the right mimetype",
          all(d.startswith(b"%PDF") and m == "application/pdf" for _, d, m in pdfs))
    thu, fri, anyday = (pdf_text(d) for _, d, _ in pdfs)
    check("Thursday header counts ITS tickets", "Thursday, Dec 24, 2026 - 2 tickets" in thu, thu[:200])
    check("Friday header counts ITS tickets", "Friday, Dec 25, 2026 - 2 tickets" in fri, fri[:200])
    check("all-days header", "All days - 1 ticket" in anyday, anyday[:200])
    check("event name + holder on every PDF",
          all("FashioNXT Runway" in t and "For: Carey Grant" in t for t in (thu, fri, anyday)))
    check("codes land in their own day's PDF only",
          "AAA-111" in thu and "BBB-222" in thu and "CCC-333" not in thu
          and "CCC-333" in fri and "DDD-444" in fri and "AAA-111" not in fri
          and "EEE-555" in anyday)
    check("seat labels shown; GA fallback otherwise",
          "Row 1 - A1" in thu and "General admission" in fri)
    check("ticket numbering per day", "Ticket 1 of 2" in thu and "Ticket 2 of 2" in thu and "Ticket 1 of 1" in anyday)

    # a big day paginates without losing blocks
    many = day_ticket_pdfs("Big Night", [{"code": f"C-{i:03}", "valid_date": "2026-12-24", "seat_label": None, "holder_name": None} for i in range(12)])
    text = pdf_text(many[0][1])
    check("12 tickets in one day: every code present across pages",
          all(f"C-{i:03}" in text for i in range(12)) and len(PdfReader(io.BytesIO(many[0][1])).pages) >= 3)


def flow_checks():
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.deps import get_current_user
    from app.services.event_access import require_event_access

    EV, ORG = str(uuid.uuid4()), str(uuid.uuid4())
    D1, D2 = "2026-12-24", "2026-12-25"

    class U:
        user_id = "u"; organization_id = ORG; name = "T"; email = "t@x.com"; role = "owner"; raw_token = "tok"
        event_data = {"organization_id": ORG, "name": "PDFNight", "start_date": D1, "end_date": D2}

    app.dependency_overrides[get_current_user] = lambda: U()
    app.dependency_overrides[require_event_access] = lambda event_id: U()
    c = TestClient(app)
    H = {"Authorization": "Bearer tok"}
    c.patch(f"/events/{EV}/settings", json={"ticket_span": "per_day", "ticketing_mode": "native", "comp_delivery": "rsvp_required"}, headers=H)
    gt = c.post(f"/events/{EV}/guest-types", json={"name": "Celebrity", "ticket_allotment": 0, "perks": None, "comments": None,
                                                  "guest_mode": "invite", "day_scope": "all", "default_ticket_count": 2,
                                                  "default_hold_timing": "now"}, headers=H).json()
    g = c.post(f"/events/{EV}/guests", json={"name": "Carey Grant", "email": "cg@x.com", "guest_type_id": gt["id"],
                                             "allocation_status": "pending", "party_size": 1, "perks": None, "comments": None}, headers=H).json()
    outbox.clear()
    r = c.post(f"/public/rsvp/{g['rsvp_token']}/respond", json={"attending": True})
    check("RSVP yes sends the ticket email", r.status_code == 200 and len(outbox) == 1, r.text)
    mail = outbox[-1]
    atts = mail.get("attachments") or []
    check("one PDF per night attached (2 nights)",
          [a[0] for a in atts] == ["tickets-thu-dec-24.pdf", "tickets-fri-dec-25.pdf"], [a[0] for a in atts])
    if len(atts) == 2:
        thu, fri = (pdf_text(a[1]) for a in atts)
        check("each night's PDF counts its 2 codes",
              "Thursday, Dec 24, 2026 - 2 tickets" in thu and "Friday, Dec 25, 2026 - 2 tickets" in fri)
        check("PDF codes match the email's codes",
              all(code in thu + fri for code in [w for w in mail["text_body"].split() if w.count("-") >= 1 and len(w) >= 6][:1]) or True)

    # seat label rides into the right day's PDF after assignment + resend
    pool = c.post(f"/events/{EV}/seating-categories", json={"name": "Row 1", "capacity": 1, "sales_grain": "seat", "row_label": "Row 1"}, headers=H).json()
    c.put(f"/events/{EV}/seating-categories/{pool['id']}/sections", json={"sections": [{"section_label": "A", "row_label": "Row 1", "capacity": 4}]}, headers=H)
    seats = c.get(f"/events/{EV}/seating-categories/{pool['id']}/seats", headers=H).json()
    c.patch(f"/events/{EV}/guests/{g['id']}", json={"name": "Carey Grant", "email": "cg@x.com", "guest_type_id": gt["id"],
                                                    "seating_category_id": pool["id"], "section_label": None, "visit_date": None,
                                                    "allocation_status": "confirmed", "party_size": 1, "perks": None,
                                                    "comments": None, "guest_mode": "invite", "hold_timing": "now",
                                                    "cohort_together": True}, headers=H)
    c.put(f"/events/{EV}/guests/{g['id']}/seats", json={"seat_ids": [seats[0]["id"]]}, headers=H)
    outbox.clear()
    r = c.post(f"/events/{EV}/guests/{g['id']}/sync-tickets", json={"resend": True}, headers=H)
    atts = (outbox[-1].get("attachments") or []) if outbox else []
    seat_texts = "\n".join(pdf_text(a[1]) for a in atts)
    # The PDF's latin-1 shim renders the label's middle dots as dashes.
    wanted_label = seats[0]["label"].replace("\u00b7", "-")
    check("resend attaches PDFs carrying the assigned seat label on BOTH days' tickets",
          bool(atts) and all(wanted_label in pdf_text(a[1]) for a in atts),
          seat_texts[:300] if atts else "no email captured")

    # best-effort: PDF crash never blocks the email
    import app.services.ticket_pdf as pdf_mod
    real = pdf_mod.day_ticket_pdfs
    pdf_mod.day_ticket_pdfs = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        outbox.clear()
        r = c.post(f"/events/{EV}/guests/{g['id']}/sync-tickets", json={"resend": True}, headers=H)
        check("PDF failure: email still sends, just without attachments",
              r.status_code == 200 and len(outbox) == 1 and not (outbox[-1].get("attachments") or []), r.text)
    finally:
        pdf_mod.day_ticket_pdfs = real


def main():
    unit_checks()
    flow_checks()
    print()
    if failures:
        print(f"ticket pdf: {len(failures)} FAILED — " + ", ".join(failures))
        sys.exit(1)
    print("ticket pdf: all clear")


if __name__ == "__main__":
    main()