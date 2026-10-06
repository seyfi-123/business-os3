from app.reports.exporters.csv_exporter import to_csv
from app.reports.exporters.excel_exporter import to_excel
from app.reports.exporters.pdf_exporter import to_pdf

EXPORTERS = {"csv": to_csv, "excel": to_excel, "xlsx": to_excel, "pdf": to_pdf}
MIME_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "excel": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
FILE_EXTS = {"csv": "csv", "excel": "xlsx", "xlsx": "xlsx", "pdf": "pdf"}
