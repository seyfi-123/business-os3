import io
from pathlib import Path
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from app.reports.types import format_cell

_ASSETS = Path(__file__).parent.parent / "assets"
_REG = _ASSETS / "DejaVuSans.ttf"
_BLD = _ASSETS / "DejaVuSans-Bold.ttf"
_REGISTERED = False
FN, FB = "Helvetica", "Helvetica-Bold"


def _register():
    global _REGISTERED, FN, FB
    if _REGISTERED:
        return
    try:
        if _REG.exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans", str(_REG)))
            FN = "DejaVuSans"
        if _BLD.exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", str(_BLD)))
            FB = "DejaVuSans-Bold"
        elif _REG.exists():
            FB = "DejaVuSans"
    except Exception:
        pass
    _REGISTERED = True


_register()
STYLES = getSampleStyleSheet()
TITLE = ParagraphStyle("T", parent=STYLES["Heading1"], fontName=FB,
                       fontSize=16, textColor=colors.HexColor("#1b2029"))
SUB = ParagraphStyle("S", parent=STYLES["Normal"], fontName=FN, fontSize=10,
                     textColor=colors.HexColor("#555555"))
SEC = ParagraphStyle("SEC", parent=STYLES["Heading2"], fontName=FB,
                     fontSize=12, textColor=colors.HexColor("#4f8cff"),
                     spaceBefore=10, spaceAfter=6)


def to_pdf(report, locale="tj"):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=15*mm, rightMargin=15*mm,
                            topMargin=15*mm, bottomMargin=15*mm)
    cur = report.meta.get("currency", "TJS")
    el = [Paragraph(_esc(report.title_tj), TITLE)]
    if report.subtitle_tj:
        el.append(Paragraph(_esc(report.subtitle_tj), SUB))
    el.append(Paragraph(_esc(f"Yaratilgan: {report.meta.get('generated_at', '')}"), SUB))
    el.append(Spacer(1, 8))
    for s in report.sections:
        el.append(Paragraph(_esc(s.title_tj), SEC))
        data = [[c.label_tj for c in s.columns]]
        for row in s.rows:
            data.append([format_cell(row.get(c.key), c.format, cur) for c in s.columns])
        if s.summary:
            data.append([format_cell(s.summary.get(c.key, ""), c.format, cur)
                         if c.key in s.summary else "" for c in s.columns])
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#4f8cff")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), FB),
            ("FONTNAME", (0,1), (-1,-1), FN),
            ("FONTSIZE", (0,0), (-1,-1), 8),
            ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#cccccc")),
            ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0,1), (-1,-1),
             [colors.white, colors.HexColor("#f5f7fb")]),
        ]))
        el.append(t)
        el.append(Spacer(1, 8))
    doc.build(el)
    return buf.getvalue()


def _esc(s):
    s = "" if s is None else str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
