"""Generate sample client/advisor PDFs (one text PDF, one scanned/image-only PDF)."""
import os
import sys

import pymupdf
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

root = sys.argv[1] if len(sys.argv) > 1 else "samples"
for r in ("client", "advisor"):
    os.makedirs(f"{root}/{r}", exist_ok=True)


def write(path, title, rows):
    c = canvas.Canvas(path, pagesize=letter)
    y = 740
    c.setFont("Helvetica-Bold", 16); c.drawString(60, y, title); y -= 40
    c.setFont("Helvetica", 11)
    c.drawString(60, y, "Date        Symbol  Side  Qty    Price     Account"); y -= 22
    for r in rows:
        c.drawString(60, y, "   ".join(r)); y -= 20
    c.save()


client = [("2026-10-20", "AAPL", "BUY", "100", "191.50", "ACC-1001"),
          ("2026-10-21", "MSFT", "SELL", "50", "405.00", "ACC-1001"),
          ("2026-10-22", "TSLA", "BUY", "20", "250.00", "ACC-1001")]
advisor = [("2026-10-20", "AAPL", "BUY", "100", "190.00", "ACC-1001"),   # client paid more -> flag
           ("2026-10-21", "MSFT", "SELL", "50", "405.00", "ACC-1001"),   # match
           ("2026-10-23", "NVDA", "BUY", "10", "900.00", "ACC-1001")]    # unmatched both sides
write(f"{root}/client/client_statement.pdf", "Client Trade Confirmations", client)
write("/tmp/_adv.pdf", "Advisor Trade Blotter", advisor)
# make the advisor PDF image-only (simulated scan)
src, out = pymupdf.open("/tmp/_adv.pdf"), pymupdf.open()
for p in src:
    img = p.get_pixmap(dpi=150)
    page = out.new_page(width=p.rect.width, height=p.rect.height)
    page.insert_image(page.rect, pixmap=img)
out.save(f"{root}/advisor/advisor_scan.pdf")
print("samples written to", root)
