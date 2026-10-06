from dataclasses import dataclass, field


@dataclass
class Column:
    key: str
    label_tj: str
    label_key: str | None = None
    align: str = "left"
    width: int = 100
    format: str | None = None


@dataclass
class Section:
    title_tj: str
    columns: list
    rows: list
    summary: dict | None = None


@dataclass
class Report:
    key: str
    title_tj: str
    subtitle_tj: str | None = None
    meta: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)

    def add(self, section):
        self.sections.append(section)
        return self


def fmt_money(v, cur="TJS"):
    try:
        return f"{float(v):,.2f} {cur}"
    except (TypeError, ValueError):
        return str(v)


def fmt_int(v):
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def fmt_pct(v):
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


def format_cell(value, fmt, cur="TJS"):
    if fmt == "money":
        return fmt_money(value, cur)
    if fmt == "int":
        return fmt_int(value)
    if fmt == "float":
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
    if fmt == "pct":
        return fmt_pct(value)
    if value is None:
        return ""
    return str(value)
