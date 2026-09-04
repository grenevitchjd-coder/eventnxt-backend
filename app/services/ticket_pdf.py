"""eventnxt-backend: app/services/ticket_pdf.py

Per-day ticket PDFs for email delivery — one PDF per event day, so a
weekend guest gets thursday.pdf / friday.pdf / saturday.pdf, each headed
with the day and HOW MANY tickets it holds ("Friday, Dec 25 — 2
tickets"), then one block per ticket: scannable QR, the code in
letters (for manual check-in), and the seat when assigned. Undated
codes (single-day events, grandfathered whole-event tickets) collect
into one "All days" PDF.

Same QR discipline as the order page (segno, micro=False so the door
camera never meets a Micro QR). fpdf2 for layout — pure generation, no
disk, returns (filename, bytes, mimetype) tuples ready for
send_email(attachments=...). Callers treat PDF failure like email
failure: best-effort, never blocking a flow.
"""
import io
from datetime import date

import segno
from fpdf import FPDF


def _latin(text: str) -> str:
    """Core PDF fonts are latin-1 only — swap the typographic characters
    the app uses (em dash, middle dot) and drop anything else exotic."""
    return (
        (text or "")
        .replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u00b7", "-")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )


def _day_label(iso: str | None) -> str:
    if not iso:
        return "All days"
    try:
        d = date.fromisoformat(iso)
        return d.strftime("%A, %b %-d, %Y")
    except ValueError:
        return iso


def _day_filename(iso: str | None) -> str:
    if not iso:
        return "tickets-all-days.pdf"
    try:
        return f"tickets-{date.fromisoformat(iso).strftime('%a-%b-%d').lower()}.pdf"
    except ValueError:
        return f"tickets-{iso}.pdf"


def _qr_png(code: str) -> bytes:
    buf = io.BytesIO()
    # micro=False is load-bearing here too — short codes must never
    # become Micro QR, which many scanner apps refuse to read.
    segno.make(code, error="m", micro=False).save(buf, kind="png", scale=6, border=2)
    return buf.getvalue()


def day_ticket_pdfs(event_name: str, tickets: list[dict]) -> list[tuple[str, bytes, str]]:
    """
    tickets: [{code, valid_date (ISO or None), seat_label (or None),
    holder_name (or None)}] → one (filename, bytes, 'application/pdf')
    per distinct day, dated days first in order, undated last.
    """
    by_day: dict = {}
    for t in tickets:
        by_day.setdefault(t.get("valid_date"), []).append(t)
    days = sorted([d for d in by_day if d]) + ([None] if None in by_day else [])

    out = []
    for day in days:
        group = by_day[day]
        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(auto=True, margin=16)
        pdf.add_page()
        pdf.set_font("helvetica", "B", 18)
        pdf.cell(0, 10, _latin(event_name), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("helvetica", "B", 13)
        n = len(group)
        pdf.cell(0, 8, _latin(f"{_day_label(day)} - {n} ticket{'s' if n != 1 else ''}"), new_x="LMARGIN", new_y="NEXT")
        holder = next((t.get("holder_name") for t in group if t.get("holder_name")), None)
        if holder:
            pdf.set_font("helvetica", "", 11)
            pdf.cell(0, 7, _latin(f"For: {holder}"), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        for i, t in enumerate(group, start=1):
            if pdf.get_y() > 235:  # keep each ticket block whole on its page
                pdf.add_page()
            y = pdf.get_y()
            png = _qr_png(t["code"])
            pdf.image(io.BytesIO(png), x=12, y=y, w=42, h=42)
            pdf.set_xy(60, y + 4)
            pdf.set_font("helvetica", "B", 12)
            pdf.cell(0, 7, f"Ticket {i} of {n}", new_x="LMARGIN", new_y="NEXT")
            pdf.set_x(60)
            pdf.set_font("courier", "B", 15)
            pdf.cell(0, 8, t["code"], new_x="LMARGIN", new_y="NEXT")
            pdf.set_x(60)
            pdf.set_font("helvetica", "", 11)
            pdf.cell(0, 7, _latin(t.get("seat_label") or "General admission / see usher"), new_x="LMARGIN", new_y="NEXT")
            pdf.set_x(60)
            pdf.set_font("helvetica", "", 9)
            pdf.set_text_color(110, 110, 110)
            pdf.cell(0, 6, "Scan the QR at the door, or give the code above for manual check-in.", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
            pdf.set_y(y + 46)
            pdf.set_draw_color(220, 220, 220)
            pdf.line(12, pdf.get_y(), 198, pdf.get_y())
            pdf.ln(4)

        out.append((_day_filename(day), bytes(pdf.output()), "application/pdf"))
    return out