"""PART B2: i18n + Alembic + Docker + Root"""
import os, subprocess

FILES = {}

# ==================== i18n ====================
FILES["backend/app/i18n/__init__.py"] = '''from app.i18n.translator import t, get_catalog, SUPPORTED_LOCALES, DEFAULT_LOCALE

__all__ = ["t", "get_catalog", "SUPPORTED_LOCALES", "DEFAULT_LOCALE"]
'''

FILES["backend/app/i18n/translator.py"] = '''from app.i18n.catalogs import CATALOGS

DEFAULT_LOCALE = "tj"
SUPPORTED_LOCALES = tuple(CATALOGS.keys())


def _lookup(cat, key):
    node = cat
    for p in key.split("."):
        if not isinstance(node, dict) or p not in node:
            return None
        node = node[p]
    return node if isinstance(node, str) else None


def t(key, locale=None, fallback=None, **params):
    locale = (locale or DEFAULT_LOCALE).lower()
    v = _lookup(CATALOGS.get(locale, {}), key)
    if v is None and locale != DEFAULT_LOCALE:
        v = _lookup(CATALOGS.get(DEFAULT_LOCALE, {}), key)
    if v is None:
        v = fallback or key
    if params:
        try:
            v = v.format(**params)
        except (KeyError, IndexError):
            pass
    return v


def get_catalog(locale):
    return CATALOGS.get(locale.lower(), CATALOGS[DEFAULT_LOCALE])


def is_supported(locale):
    return (locale or "").lower() in SUPPORTED_LOCALES
'''

FILES["backend/app/i18n/resolver.py"] = '''from fastapi import Request

DEFAULT_LOCALE = "tj"
SUPPORTED = ("tj", "ru", "en")


def resolve_locale(request: Request) -> str:
    lang = request.query_params.get("lang", "").lower()
    if lang in SUPPORTED:
        return lang
    header = request.headers.get("accept-language", "")
    for part in header.split(","):
        code = part.split(";")[0].strip().lower().split("-")[0]
        if code in SUPPORTED:
            return code
    return DEFAULT_LOCALE
'''

FILES["backend/app/i18n/api.py"] = '''from fastapi import APIRouter, Request, HTTPException
from app.i18n.translator import get_catalog, SUPPORTED_LOCALES
from app.i18n.resolver import resolve_locale

router = APIRouter(prefix="/api/i18n", tags=["i18n"])


@router.get("/locales")
def list_locales():
    return {"supported": list(SUPPORTED_LOCALES), "default": "tj"}


@router.get("/catalog")
def catalog(request: Request, locale: str | None = None):
    loc = (locale or resolve_locale(request)).lower()
    if loc not in SUPPORTED_LOCALES:
        raise HTTPException(400, f"Unsupported locale: {loc}")
    return {"locale": loc, "catalog": get_catalog(loc)}
'''

FILES["backend/app/i18n/catalogs/__init__.py"] = '''from app.i18n.catalogs.tj import CATALOG as TJ
from app.i18n.catalogs.ru import CATALOG as RU
from app.i18n.catalogs.en import CATALOG as EN

CATALOGS = {"tj": TJ, "ru": RU, "en": EN}
'''

FILES["backend/app/i18n/catalogs/tj.py"] = '''CATALOG = {
    "common": {"yes": "Ҳа", "no": "Йўқ", "cancel": "Бекор қилиш",
               "save": "Сақлаш", "delete": "Ўчириш", "total": "Жами",
               "status": "Ҳолат", "period": "Давр", "from": "Дан",
               "to": "Гача", "branch": "Филиал", "product": "Маҳсулот",
               "supplier": "Supplier", "customer": "Мижоз",
               "quantity": "Миқдор", "amount": "Сумма", "date": "Сана",
               "generated_at": "Яратилган"},
    "reports": {"pnl": {"title": "Фойда ва зарар ҳисоботи (P&L)",
                        "financial": "Молиявий кўрсаткичлар",
                        "kpi": "Маржа ва KPI", "revenue": "Тушум",
                        "cogs": "COGS", "gross_profit": "Ялпи фойда",
                        "expenses": "Операцион харажат",
                        "net_profit": "Соф фойда",
                        "item": "Кўрсаткич", "value": "Қиймат"}},
    "errors": {"unauthorized": "Авторизация талаб қилинади",
               "forbidden": "Рухсат йўқ", "not_found": "Топилмади",
               "payment_required": "Тарifингизда бу функция мавжуд эмас",
               "limit_reached": "Лимит тугади",
               "invalid_input": "Нотўғри маълумот"},
}
'''

FILES["backend/app/i18n/catalogs/ru.py"] = '''CATALOG = {
    "common": {"yes": "Да", "no": "Нет", "cancel": "Отмена",
               "save": "Сохранить", "delete": "Удалить", "total": "Итого",
               "status": "Статус", "period": "Период", "from": "С",
               "to": "По", "branch": "Филиал", "product": "Товар",
               "supplier": "Поставщик", "customer": "Клиент",
               "quantity": "Количество", "amount": "Сумма", "date": "Дата",
               "generated_at": "Создано"},
    "reports": {"pnl": {"title": "Отчёт о прибылях и убытках (P&L)",
                        "financial": "Финансовые показатели",
                        "kpi": "Маржа и KPI", "revenue": "Выручка",
                        "cogs": "COGS", "gross_profit": "Валовая прибыль",
                        "expenses": "Операционные расходы",
                        "net_profit": "Чистая прибыль",
                        "item": "Показатель", "value": "Значение"}},
    "errors": {"unauthorized": "Требуется авторизация",
               "forbidden": "Доступ запрещён", "not_found": "Не найдено",
               "payment_required": "Функция недоступна",
               "limit_reached": "Лимит исчерпан",
               "invalid_input": "Некорректные данные"},
}
'''

FILES["backend/app/i18n/catalogs/en.py"] = '''CATALOG = {
    "common": {"yes": "Yes", "no": "No", "cancel": "Cancel",
               "save": "Save", "delete": "Delete", "total": "Total",
               "status": "Status", "period": "Period", "from": "From",
               "to": "To", "branch": "Branch", "product": "Product",
               "supplier": "Supplier", "customer": "Customer",
               "quantity": "Quantity", "amount": "Amount", "date": "Date",
               "generated_at": "Generated"},
    "reports": {"pnl": {"title": "Profit & Loss (P&L)",
                        "financial": "Financial Indicators",
                        "kpi": "Margin & KPI", "revenue": "Revenue",
                        "cogs": "COGS", "gross_profit": "Gross Profit",
                        "expenses": "Operating Expenses",
                        "net_profit": "Net Profit",
                        "item": "Indicator", "value": "Value"}},
    "errors": {"unauthorized": "Authorization required",
               "forbidden": "Access denied", "not_found": "Not found",
               "payment_required": "Feature not available",
               "limit_reached": "Limit reached",
               "invalid_input": "Invalid input"},
}
'''

# ==================== ROOT ====================
FILES["backend/requirements.txt"] = '''fastapi==0.115.0
uvicorn[standard]==0.32.0
sqlalchemy==2.0.35
psycopg2-binary==2.9.9
pydantic==2.9.2
pydantic-settings==2.5.2
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.12
alembic==1.13.3
openai==1.51.0
openpyxl==3.1.5
reportlab==4.2.2
pytest==8.3.3
httpx==0.27.2
'''

FILES["backend/alembic.ini"] = '''[alembic]
script_location = alembic
sqlalchemy.url = postgresql://postgres:postgres@localhost:5432/business_os

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
'''

FILES["backend/.env.example"] = '''APP_NAME="Business OS"
SECRET_KEY="generate-a-long-random-string-here"
ACCESS_TOKEN_EXPIRE_MINUTES=10080
DATABASE_URL="postgresql://postgres:postgres@localhost:5432/business_os"
OPENAI_API_KEY=""
OPENAI_MODEL="gpt-4o-mini"
BILLING_PAYMENT_MODE="DEMO"
TELEGRAM_BOT_TOKEN=""
SMTP_HOST=""
SMTP_PORT=587
SMTP_USER=""
SMTP_PASSWORD=""
SMTP_FROM=""
'''

FILES[".gitignore"] = '''__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
*.log
node_modules/
.next/
.env
.env.local
.env.production
.vscode/
.idea/
.DS_Store
Thumbs.db
backend/app/reports/assets/*.ttf
certs/
certs-data/
'''

FILES["README.md"] = '''# Business OS

Universal Business Intelligence & Operations Platform

## Quick Start

### Local
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
createdb business_os
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
