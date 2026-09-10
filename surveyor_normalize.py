"""
Surveyor attendance -- parsing and validation.

Source is a published Google Form response sheet. The form records what a
surveyor DID submit; absence is the absence of a row. So "attendance" is only
meaningful against an explicit expectation (roster x working days), which the
app supplies -- this module deliberately does not guess at one.

Two frames come out of load():
  daily     -- one row per submission. Use for hours, tickets, distance.
  ward_long -- one row per (submission, ward). Use ONLY for ward coverage;
               tickets and distance are repeated across a submission's wards,
               so summing them here double-counts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

SHEET_PUB = (
    "https://docs.google.com/spreadsheets/d/e/"
    "2PACX-1vRTuhd4_d6dJSNRPi8RWyyC5tPjiTu4ZlVcfNB-lM3PPESYho3WukxlfPdpaivkyAFQDuf1b82stp3w"
    "/pub?gid=2126902011&single=true&output=csv"
)

DATA_FILENAMES = ("surveyor_attendance.csv", "surveyor_attendance.xlsx")

# Form headers carry trailing spaces ('Name ', 'Zone '), so every lookup goes
# through the stripped-and-lowercased alias map below rather than exact names.
COLUMN_ALIASES = {
    "timestamp": "submitted_at",
    "date": "date",
    "name": "surveyor",
    "start time": "start_time",
    "end time": "end_time",
    "zone": "zone",
    "number of tickets": "tickets",
    "no. of tickets raised": "tickets",
    "tickets raised": "tickets",
    "distance covered": "distance_km",
    "distance": "distance_km",
    "ward": "ward",
}

# Anything beyond these is flagged rather than dropped -- a 20-hour shift is
# more likely a typo than a fact, but it is the user's call.
MAX_PLAUSIBLE_HOURS = 14
MAX_PLAUSIBLE_DISTANCE_KM = 200

FY_QUARTERS = {
    1: "Q4", 2: "Q4", 3: "Q4",
    4: "Q1", 5: "Q1", 6: "Q1",
    7: "Q2", 8: "Q2", 9: "Q2",
    10: "Q3", 11: "Q3", 12: "Q3",
}


@dataclass
class LoadReport:
    """Everything needing a human decision, surfaced rather than swallowed."""
    rows_in: int = 0
    rows_out: int = 0
    source: str = ""
    missing_columns: list[str] = field(default_factory=list)
    unparsed_dates: int = 0
    unparsed_times: int = 0
    end_before_start: int = 0
    implausible_hours: int = 0
    implausible_distance: int = 0
    missing_surveyor: int = 0
    missing_zone: int = 0
    unparsed_wards: dict[str, int] = field(default_factory=dict)
    duplicate_submissions: int = 0
    duplicates_removed: int = 0

    @property
    def is_clean(self) -> bool:
        return not (
            self.missing_columns or self.unparsed_dates or self.unparsed_times
            or self.end_before_start or self.implausible_hours
            or self.implausible_distance or self.missing_surveyor
            or self.unparsed_wards or self.duplicate_submissions
        )

    def render(self) -> str:
        lines = [f"{self.rows_in} rows in, {self.rows_out} rows out"]
        if self.source:
            lines.append(f"source: {self.source}")
        if self.missing_columns:
            lines.append(f"MISSING COLUMNS: {', '.join(self.missing_columns)}")
        checks = [
            ("unparseable Date", self.unparsed_dates),
            ("unparseable Start/End Time", self.unparsed_times),
            ("End Time before Start Time", self.end_before_start),
            (f"shift longer than {MAX_PLAUSIBLE_HOURS}h", self.implausible_hours),
            (f"distance over {MAX_PLAUSIBLE_DISTANCE_KM} km", self.implausible_distance),
            ("blank Name", self.missing_surveyor),
            ("blank Zone", self.missing_zone),
            ("duplicate surveyor+date submissions", self.duplicate_submissions),
        ]
        for label, count in checks:
            if count:
                lines.append(f"{label}: {count}")
        for value, count in sorted(self.unparsed_wards.items(), key=lambda kv: -kv[1]):
            lines.append(f"UNPARSEABLE ward value: {value!r} ({count} rows)")
        if self.duplicates_removed:
            lines.append(
                f"kept latest submission only, dropped {self.duplicates_removed}"
            )
        if self.is_clean:
            lines.append("clean")
        return "\n".join(lines)


def _rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    mapping = {}
    for col in df.columns:
        key = re.sub(r"\s+", " ", str(col).replace("\xa0", " ")).strip().lower()
        if key in COLUMN_ALIASES:
            mapping[col] = COLUMN_ALIASES[key]
    return df.rename(columns=mapping)


def _parse_clock(series: pd.Series) -> pd.Series:
    """'16:15:00' or '4:15 PM' -> hours after midnight as a float."""
    text = series.astype("string").str.strip()

    parsed = pd.to_datetime(text, format="%H:%M:%S", errors="coerce")
    for fmt in ("%H:%M", "%I:%M:%S %p", "%I:%M %p"):
        gaps = parsed.isna() & text.notna()
        if not gaps.any():
            break
        parsed = parsed.fillna(pd.to_datetime(text[gaps], format=fmt, errors="coerce"))

    return parsed.dt.hour + parsed.dt.minute / 60 + parsed.dt.second / 3600


def _split_wards(value: object) -> list[str]:
    """'16, 20' -> ['16', '20']. Separators: comma, slash, semicolon, '&', 'and'."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return []
    parts = re.split(r"[,;/&]|\band\b", text, flags=re.I)
    out = []
    for part in parts:
        part = part.strip().strip(".")
        if not part:
            continue
        digits = re.fullmatch(r"(?:ward\s*)?(\d+)", part, flags=re.I)
        out.append(digits.group(1) if digits else part)
    return out


def fy_label(ts: pd.Timestamp) -> str:
    """Indian financial year: April to March. Apr 2026 -> 'FY 2026-27'."""
    if pd.isna(ts):
        return "Unknown"
    start = ts.year if ts.month >= 4 else ts.year - 1
    return f"FY {start}-{str(start + 1)[-2:]}"


def load(
    source: str | bytes,
    filename: str = "",
    dayfirst: bool = True,
    dedupe: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, LoadReport]:
    """Read and normalise the response sheet.

    `source` is a URL, a path, or raw bytes (with `filename` to pick a reader).
    `dayfirst` controls date parsing -- Google Forms writes in the sheet's
    locale, so this must match it or 10/09 silently becomes 9 October.
    `dedupe` keeps only the latest submission per surveyor+date.
    """
    if isinstance(source, bytes):
        import io
        buf = io.BytesIO(source)
        raw = (
            pd.read_excel(buf)
            if filename.lower().endswith((".xlsx", ".xlsm", ".xls"))
            else pd.read_csv(buf)
        )
        label = filename or "upload"
    elif str(source).lower().endswith((".xlsx", ".xlsm", ".xls")):
        raw = pd.read_excel(source)
        label = str(source)
    else:
        raw = pd.read_csv(source)
        label = "published sheet" if str(source).startswith("http") else str(source)

    report = LoadReport(rows_in=len(raw), source=label)
    df = _rename_columns(raw).dropna(how="all")

    for needed in ("date", "surveyor"):
        if needed not in df.columns:
            report.missing_columns.append(needed)
    if report.missing_columns:
        return pd.DataFrame(), pd.DataFrame(), report

    out = pd.DataFrame(index=df.index)

    out["date"] = pd.to_datetime(df["date"], dayfirst=dayfirst, errors="coerce")
    report.unparsed_dates = int(out["date"].isna().sum())

    if "submitted_at" in df.columns:
        out["submitted_at"] = pd.to_datetime(
            df["submitted_at"], dayfirst=dayfirst, errors="coerce"
        )
    else:
        out["submitted_at"] = pd.NaT

    out["surveyor_raw"] = df["surveyor"].astype("string").str.strip()
    # Group on a case-folded key so 'suraj' and 'Suraj' are one person, then
    # show every row under one spelling -- otherwise a stray lowercase entry
    # splits someone into two rows in every table on the dashboard.
    out["surveyor_key"] = out["surveyor_raw"].str.casefold()
    def best_spelling(names: pd.Series) -> str:
        # Most frequent spelling wins; ties prefer one that is not all
        # lower-case, so 'Suraj' beats 'suraj' rather than depending on which
        # row happened to arrive first.
        counts = names.value_counts()
        top = counts[counts == counts.max()].index.tolist()
        cased = [n for n in top if n != n.lower()]
        return sorted(cased or top)[0]

    display = (
        out.dropna(subset=["surveyor_raw"])
        .groupby("surveyor_key")["surveyor_raw"].agg(best_spelling)
    )
    out["surveyor"] = out["surveyor_key"].map(display)
    report.missing_surveyor = int(
        (out["surveyor_raw"].isna() | (out["surveyor_raw"] == "")).sum()
    )

    out["zone"] = (
        df["zone"].astype("string").str.strip() if "zone" in df.columns
        else pd.Series(pd.NA, index=df.index, dtype="string")
    )
    report.missing_zone = int((out["zone"].isna() | (out["zone"] == "")).sum())

    if "start_time" in df.columns and "end_time" in df.columns:
        start = _parse_clock(df["start_time"])
        end = _parse_clock(df["end_time"])
        out["start_hour"] = start
        out["end_hour"] = end
        report.unparsed_times = int(
            (start.isna() | end.isna()).sum()
            - (df["start_time"].isna() & df["end_time"].isna()).sum()
        )
        hours = end - start
        report.end_before_start = int((hours < 0).sum())
        hours = hours.where(hours >= 0)
        out["hours_worked"] = hours.round(2)
        report.implausible_hours = int((hours > MAX_PLAUSIBLE_HOURS).sum())
    else:
        out["start_hour"] = out["end_hour"] = out["hours_worked"] = pd.NA
        report.missing_columns.append("start/end time")

    out["tickets"] = (
        pd.to_numeric(df["tickets"], errors="coerce") if "tickets" in df.columns else pd.NA
    )
    out["distance_km"] = (
        pd.to_numeric(df["distance_km"], errors="coerce")
        if "distance_km" in df.columns else pd.NA
    )
    if "distance_km" in df.columns:
        report.implausible_distance = int(
            (out["distance_km"] > MAX_PLAUSIBLE_DISTANCE_KM).sum()
        )

    raw_ward = (
        df["ward"] if "ward" in df.columns
        else pd.Series(pd.NA, index=df.index, dtype="object")
    )
    out["ward_raw"] = raw_ward.astype("string").str.strip()
    ward_lists = raw_ward.map(_split_wards)
    out["wards"] = ward_lists
    out["ward_count"] = ward_lists.map(len)

    bad_wards = {}
    for value, parts in zip(out["ward_raw"], ward_lists):
        if pd.notna(value) and value != "" and not parts:
            bad_wards[value] = bad_wards.get(value, 0) + 1
    report.unparsed_wards = bad_wards

    # Calendar helpers used by the filters.
    out["month"] = out["date"].dt.to_period("M").astype("string")
    out["iso_week"] = out["date"].dt.strftime("%G-W%V")
    out["fy"] = out["date"].map(fy_label)
    out["fy_quarter"] = out["date"].dt.month.map(FY_QUARTERS)
    out["fy_quarter_label"] = out["fy"] + " " + out["fy_quarter"].astype("string")
    out["weekday"] = out["date"].dt.day_name()

    dupes = out.duplicated(subset=["surveyor_key", "date"], keep=False)
    report.duplicate_submissions = int(dupes.sum())
    if dedupe and dupes.any():
        before = len(out)
        out = (
            out.sort_values("submitted_at")
            .drop_duplicates(subset=["surveyor_key", "date"], keep="last")
            .sort_index()
        )
        report.duplicates_removed = before - len(out)

    out["is_duplicate"] = out.duplicated(subset=["surveyor_key", "date"], keep=False)

    report.rows_out = len(out)
    daily = out.reset_index(drop=True)

    ward_long = (
        daily.explode("wards")
        .rename(columns={"wards": "ward"})
        .dropna(subset=["ward"])
        .reset_index(drop=True)
    )
    return daily, ward_long, report


def attendance_matrix(
    daily: pd.DataFrame,
    expected_dates: list[pd.Timestamp],
    roster: list[str] | None = None,
) -> pd.DataFrame:
    """Surveyor x date grid of days logged.

    `expected_dates` and `roster` are the expectation the sheet cannot supply:
    a missing row means 'no submission', which is only absence if that person
    was due to work that day.
    """
    names = roster or sorted(daily["surveyor"].dropna().unique().tolist())
    logged = set(zip(daily["surveyor"], daily["date"]))
    grid = pd.DataFrame(
        [[1 if (n, d) in logged else 0 for d in expected_dates] for n in names],
        index=names,
        columns=[d.strftime("%d %b") for d in expected_dates],
    )
    grid["Days logged"] = grid.sum(axis=1)
    if expected_dates:
        grid["Attendance %"] = (
            grid["Days logged"] / len(expected_dates) * 100
        ).round(1)
    return grid


def surveyor_summary(daily: pd.DataFrame) -> pd.DataFrame:
    """Per-surveyor performance table."""
    if daily.empty:
        return pd.DataFrame()

    grouped = daily.groupby("surveyor", dropna=True)
    summary = pd.DataFrame({
        "Days logged": grouped.size(),
        "Total hours": grouped["hours_worked"].sum().round(1),
        "Avg hours/day": grouped["hours_worked"].mean().round(2),
        "Total tickets": grouped["tickets"].sum(),
        "Avg tickets/day": grouped["tickets"].mean().round(1),
        "Total distance (km)": grouped["distance_km"].sum().round(1),
        "Avg distance/day": grouped["distance_km"].mean().round(1),
        "Wards touched": grouped["ward_count"].sum(),
        "Zones": grouped["zone"].nunique(),
    })
    hours = pd.to_numeric(summary["Total hours"], errors="coerce").replace(0, pd.NA)
    tickets = pd.to_numeric(summary["Total tickets"], errors="coerce")
    # to_numeric first: nullable dtypes raise on .round() when NA is present.
    summary["Tickets/hour"] = pd.to_numeric(
        tickets / hours, errors="coerce"
    ).round(2)
    return summary.sort_values("Total tickets", ascending=False)
