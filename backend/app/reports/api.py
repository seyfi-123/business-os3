from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.deps import require
from app.core.validators import ensure_branch
from app.models.user import User
from app.reports.catalog import CATALOG
from app.reports.builders import BUILDERS
from app.reports.exporters import EXPORTERS, MIME_TYPES, FILE_EXTS

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/catalog")
def catalog():
    return CATALOG


def _period(since_str, until_str):
    if until_str and len(until_str) == 10:
        until_dt = datetime.fromisoformat(until_str) + timedelta(days=1)
    elif until_str:
        until_dt = datetime.fromisoformat(until_str)
    else:
        until_dt = datetime.utcnow() + timedelta(seconds=1)
    if since_str:
        since_dt = datetime.fromisoformat(since_str)
    else:
        since_dt = until_dt - timedelta(days=30)
    return since_dt, until_dt


@router.get("/{report_key}.{fmt}")
def download(report_key: str, fmt: str, request: Request,
             since: str | None = Query(default=None),
             until: str | None = Query(default=None),
             branch_id: int | None = None,
             user: User = Depends(require("reports", "view")),
             db: Session = Depends(get_db)):
    if report_key not in BUILDERS:
        raise HTTPException(404, f"Nomahlum report: {report_key}")
    if fmt not in EXPORTERS:
        raise HTTPException(400, f"Nomahlum format: {fmt}")
    try:
        since_dt, until_dt = _period(since, until)
    except ValueError:
        raise HTTPException(400, "Notogri sana formati")
    if branch_id is not None:
        ensure_branch(db, user.tenant_id, branch_id)
    builder = BUILDERS[report_key]
    report = builder(db, user.tenant_id, since_dt, until_dt, branch_id=branch_id)
    report.meta["until_exclusive"] = True
    locale = "tj"
    try:
        from app.i18n.resolver import resolve_locale
        locale = resolve_locale(request)
    except Exception:
        pass
    exporter = EXPORTERS[fmt]
    try:
        content = exporter(report, locale=locale)
    except TypeError:
        content = exporter(report)
    fn = f"{report_key}_{since_dt.date()}_{until_dt.date()}.{FILE_EXTS[fmt]}"
    return Response(content=content, media_type=MIME_TYPES[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{fn}"',
                             "Cache-Control": "no-store"})
