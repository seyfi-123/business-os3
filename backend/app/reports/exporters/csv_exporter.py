import csv
import io
from app.reports.types import format_cell


def to_csv(report, locale="tj"):
    buf = io.StringIO()
    w = csv.writer(buf)
    cur = report.meta.get("currency", "TJS")
    w.writerow([report.title_tj])
    if report.subtitle_tj:
        w.writerow([report.subtitle_tj])
    w.writerow([])
    for s in report.sections:
        w.writerow([s.title_tj])
        w.writerow([c.label_tj for c in s.columns])
        for row in s.rows:
            w.writerow([format_cell(row.get(c.key), c.format, cur) for c in s.columns])
        if s.summary:
            w.writerow([format_cell(s.summary.get(c.key, ""), c.format, cur)
                        if c.key in s.summary else "" for c in s.columns])
        w.writerow([])
    return buf.getvalue().encode("utf-8-sig")
