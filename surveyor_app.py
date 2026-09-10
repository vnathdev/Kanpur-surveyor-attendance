"""
Kanpur -- Surveyor Attendance & Performance Dashboard (NCAP DSP)

Reads the published Google Form response sheet. On a network that blocks
docs.google.com the fetch fails, so a local file or an upload can be used
instead -- the active source is always named on screen.

Run:  streamlit run surveyor_app.py
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from surveyor_normalize import (
    DATA_FILENAMES,
    MAX_PLAUSIBLE_HOURS,
    SHEET_PUB,
    attendance_matrix,
    load,
    surveyor_summary,
)

CITY = "Kanpur"

st.set_page_config(
    page_title=f"{CITY} Surveyor Dashboard",
    page_icon="📋",
    layout="wide",
    initial_sidebar_state="expanded",
)

try:
    APP_DIR = Path(__file__).resolve().parent
except NameError:
    APP_DIR = Path.cwd()

VIEWS = [
    "Overview",
    "Attendance Calendar",
    "Surveyor Performance",
    "Zone & Ward Coverage",
    "Trends",
    "Daily Log",
    "Data Quality",
]

ACCENT = "#3B71CA"
GOOD = "#10B981"
WARN = "#F59E0B"


def _full_width() -> dict:
    """Streamlit renamed the full-width flag in 1.49; pick per version."""
    try:
        major, minor = (int(part) for part in st.__version__.split(".")[:2])
    except (ValueError, AttributeError):
        return {"use_container_width": True}
    if (major, minor) >= (1, 49):
        return {"width": "stretch"}
    return {"use_container_width": True}


FULL_WIDTH = _full_width()


# ==========================================
# DATA LOADING
# ==========================================

@st.cache_data(ttl=300, show_spinner="Loading surveyor submissions…")
def load_from_sheet(dayfirst: bool, dedupe: bool):
    return load(SHEET_PUB, dayfirst=dayfirst, dedupe=dedupe)


@st.cache_data(ttl=300, show_spinner="Loading surveyor submissions…")
def load_from_path(path: str, dayfirst: bool, dedupe: bool):
    return load(path, dayfirst=dayfirst, dedupe=dedupe)


@st.cache_data(show_spinner="Reading upload…")
def load_from_bytes(data: bytes, filename: str, dayfirst: bool, dedupe: bool):
    return load(data, filename=filename, dayfirst=dayfirst, dedupe=dedupe)


def download_csv(df: pd.DataFrame, label: str, filename: str, index: bool = False):
    st.download_button(
        label, df.to_csv(index=index).encode("utf-8"),
        file_name=filename, mime="text/csv",
    )


def metric_row(items: list[tuple[str, str]]):
    for col, (label, value) in zip(st.columns(len(items)), items):
        col.metric(label, value)


def fmt(value, suffix: str = "", nd: int = 1) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:,.{nd}f}{suffix}"


# ==========================================
# SIDEBAR: SOURCE
# ==========================================

st.title(f"📋 {CITY} Surveyor Attendance & Performance")
st.markdown("---")

st.sidebar.header("📂 Data Source")

local_candidates = [APP_DIR / name for name in DATA_FILENAMES if (APP_DIR / name).exists()]
source_options = ["Published sheet (live)"]
if local_candidates:
    source_options.append(f"Local file ({local_candidates[0].name})")
source_options.append("Upload a file")

source_choice = st.sidebar.radio("Read submissions from", source_options, index=0)

upload = None
if source_choice == "Upload a file":
    upload = st.sidebar.file_uploader(
        "Form responses (CSV or XLSX)", type=["csv", "xlsx", "xls"]
    )

with st.sidebar.expander("⚙️ Parsing options"):
    dayfirst = st.checkbox(
        "Dates are day-first (DD/MM/YYYY)", value=True,
        help="Google Forms writes dates in the sheet's locale. Get this wrong "
             "and 10/09 becomes 9 October instead of 10 September.",
    )
    dedupe = st.checkbox(
        "Keep only the latest submission per surveyor per day", value=False,
        help="Surveyors sometimes submit the form twice. Off by default so "
             "you can see the duplicates first in Data Quality.",
    )
    if st.button("↻ Reload data"):
        st.cache_data.clear()
        st.rerun()

error = None
try:
    if source_choice == "Upload a file":
        if upload is None:
            st.info("⬅️ Upload the Form response export in the sidebar to begin.")
            st.stop()
        daily, ward_long, report = load_from_bytes(
            upload.getvalue(), upload.name, dayfirst, dedupe
        )
    elif source_choice.startswith("Local file"):
        daily, ward_long, report = load_from_path(
            str(local_candidates[0]), dayfirst, dedupe
        )
    else:
        daily, ward_long, report = load_from_sheet(dayfirst, dedupe)
except Exception as exc:
    error = f"{type(exc).__name__}: {exc}"
    daily = ward_long = pd.DataFrame()
    report = None

if error:
    st.error(
        f"**Could not read the submissions.** If this machine cannot reach "
        f"docs.google.com, download the response sheet as CSV and either save "
        f"it beside `surveyor_app.py` as `surveyor_attendance.csv` or pick "
        f"*Upload a file* in the sidebar.\n\n`{error}`"
    )
    st.stop()

if daily.empty:
    st.warning("⚠️ No submissions found in this source.")
    if report is not None:
        st.code(report.render(), language=None)
    st.stop()

st.caption(
    f"**{len(daily)} submissions** from **{daily['surveyor'].nunique()} surveyors**, "
    f"read from {report.source}."
)

if report.unparsed_dates:
    st.warning(
        f"⚠️ {report.unparsed_dates} submissions have an unreadable date and are "
        f"excluded from anything date-based. See *Data Quality*."
    )
if report.duplicate_submissions and not dedupe:
    st.warning(
        f"⚠️ {report.duplicate_submissions} submissions share a surveyor and date. "
        f"Turn on de-duplication in the sidebar to count only the latest."
    )

st.sidebar.markdown("---")
st.sidebar.header("🧭 Navigation")

if "sv_view" not in st.session_state:
    st.session_state.sv_view = VIEWS[0]

for _view in VIEWS:
    _kind = "primary" if st.session_state.sv_view == _view else "secondary"
    if st.sidebar.button(_view, type=_kind, **FULL_WIDTH):
        st.session_state.sv_view = _view
        st.rerun()


# ==========================================
# SIDEBAR: FILTERS
# ==========================================

dated = daily.dropna(subset=["date"])

st.sidebar.markdown("---")
st.sidebar.header("🔎 Filters")

period_mode = st.sidebar.selectbox(
    "Period", ["All time", "Date range", "Month", "ISO week", "FY quarter"]
)

mask = pd.Series(True, index=daily.index)
period_label = "All time"

if not dated.empty:
    if period_mode == "Date range":
        lo, hi = dated["date"].min().date(), dated["date"].max().date()
        picked = st.sidebar.date_input(
            "Between", value=(lo, hi), min_value=lo, max_value=hi
        )
        if isinstance(picked, tuple) and len(picked) == 2:
            start, end = picked
            mask &= daily["date"].dt.date.between(start, end)
            period_label = f"{start:%d %b %Y} – {end:%d %b %Y}"
    elif period_mode == "Month":
        months = sorted(dated["month"].dropna().unique().tolist(), reverse=True)
        pick = st.sidebar.selectbox("Month", months)
        mask &= daily["month"] == pick
        period_label = pick
    elif period_mode == "ISO week":
        weeks = sorted(dated["iso_week"].dropna().unique().tolist(), reverse=True)
        pick = st.sidebar.selectbox("Week", weeks)
        mask &= daily["iso_week"] == pick
        period_label = pick
    elif period_mode == "FY quarter":
        quarters = sorted(
            dated["fy_quarter_label"].dropna().unique().tolist(), reverse=True
        )
        pick = st.sidebar.selectbox("FY quarter", quarters)
        mask &= daily["fy_quarter_label"] == pick
        period_label = pick

all_surveyors = sorted(daily["surveyor"].dropna().unique().tolist())
picked_surveyors = st.sidebar.multiselect(
    "Surveyor", all_surveyors, default=all_surveyors
)
mask &= daily["surveyor"].isin(picked_surveyors)

all_zones = sorted(daily["zone"].dropna().unique().tolist())
if all_zones:
    picked_zones = st.sidebar.multiselect("Zone", all_zones, default=all_zones)
    mask &= daily["zone"].isin(picked_zones) | daily["zone"].isna()

df = daily[mask]
wl = ward_long[ward_long.index.isin(df.index)] if not ward_long.empty else ward_long
# ward_long was built from daily before filtering, so re-derive from df instead
# of trusting index alignment after a reset.
if not df.empty:
    wl = (
        df.explode("wards").rename(columns={"wards": "ward"})
        .dropna(subset=["ward"])
    )

view = st.session_state.sv_view

if df.empty:
    st.warning("⚠️ No submissions match the current filters.")
    st.stop()

st.markdown(f"###### Showing **{period_label}** · {len(df)} submissions")


# ==========================================
# OVERVIEW
# ==========================================
if view == "Overview":
    st.subheader("📈 Overview")

    metric_row([
        ("👥 Surveyors active", f"{df['surveyor'].nunique()}"),
        ("📆 Days covered", f"{df['date'].dt.date.nunique()}"),
        ("📝 Submissions", f"{len(df):,}"),
        ("🎫 Tickets raised", fmt(df["tickets"].sum(), nd=0)),
    ])
    metric_row([
        ("⏱️ Total hours", fmt(df["hours_worked"].sum())),
        ("⏱️ Avg hours/day", fmt(df["hours_worked"].mean(), nd=2)),
        ("🛣️ Distance (km)", fmt(df["distance_km"].sum())),
        ("🎫 Tickets/day", fmt(df["tickets"].mean())),
    ])

    st.markdown("---")
    st.markdown("##### 📅 Daily Submissions")
    per_day = (
        df.dropna(subset=["date"]).groupby(df["date"].dt.date)
        .agg(Submissions=("surveyor", "nunique"),
             Tickets=("tickets", "sum"),
             Hours=("hours_worked", "sum"))
        .reset_index().rename(columns={"date": "Day"})
    )
    if len(per_day) > 1:
        long = per_day.melt("Day", var_name="Measure", value_name="Value")
        st.altair_chart(
            alt.Chart(long).mark_line(point=True).encode(
                x=alt.X("Day:T", title=None),
                y=alt.Y("Value:Q", title=None),
                color=alt.Color("Measure:N", legend=alt.Legend(orient="top", title=None)),
                tooltip=["Day:T", "Measure:N", "Value:Q"],
            ).properties(height=300),
            **FULL_WIDTH,
        )
    else:
        st.caption("Daily trend needs more than one day of submissions.")
    st.dataframe(per_day, hide_index=True, **FULL_WIDTH)

    st.markdown("##### 🏅 Tickets by Surveyor")
    by_sv = (
        df.groupby("surveyor")
        .agg(Tickets=("tickets", "sum"), Hours=("hours_worked", "sum"),
             Days=("date", "nunique"))
        .reset_index().sort_values("Tickets", ascending=False)
    )
    st.altair_chart(
        alt.Chart(by_sv).mark_bar(color=ACCENT).encode(
            x=alt.X("Tickets:Q", title="Tickets raised"),
            y=alt.Y("surveyor:N", sort="-x", title=None),
            tooltip=["surveyor", "Tickets", "Hours", "Days"],
        ).properties(height=max(200, 30 * len(by_sv))),
        **FULL_WIDTH,
    )


# ==========================================
# ATTENDANCE CALENDAR
# ==========================================
elif view == "Attendance Calendar":
    st.subheader("🗓️ Attendance Calendar")
    st.info(
        "The form records who **did** submit. A missing row only means absence "
        "if that person was expected to work that day — so set the roster and "
        "working days below rather than reading the blanks as absence."
    )

    dated_df = df.dropna(subset=["date"])
    if dated_df.empty:
        st.warning("⚠️ No submissions with a readable date.")
    else:
        lo, hi = dated_df["date"].min().date(), dated_df["date"].max().date()

        c1, c2 = st.columns(2)
        with c1:
            span = st.date_input(
                "Working-day window", value=(lo, hi), key="att_span"
            )
        with c2:
            skip = st.multiselect(
                "Treat as non-working days",
                ["Sunday", "Saturday"], default=["Sunday"],
                help="Public holidays are not in the data; exclude them by "
                     "narrowing the window if they distort a month.",
            )

        if not (isinstance(span, tuple) and len(span) == 2):
            st.info("Pick a start and end date.")
        else:
            start, end = span
            days = [
                pd.Timestamp(start + timedelta(days=i))
                for i in range((end - start).days + 1)
            ]
            days = [d for d in days if d.day_name() not in skip]

            roster_mode = st.radio(
                "Roster",
                ["Surveyors seen in this data", "Paste a roster"],
                horizontal=True,
            )
            roster = None
            if roster_mode == "Paste a roster":
                text = st.text_area(
                    "One name per line — names not in the data will show as 0 days",
                    height=120,
                )
                roster = [n.strip() for n in text.splitlines() if n.strip()] or None

            if not days:
                st.warning("⚠️ The window contains no working days.")
            else:
                grid = attendance_matrix(dated_df, days, roster)
                st.caption(
                    f"{len(days)} working days in window "
                    f"({start:%d %b} – {end:%d %b}), excluding {', '.join(skip) or 'nothing'}."
                )

                metric_row([
                    ("👥 On roster", f"{len(grid)}"),
                    ("📆 Working days", f"{len(days)}"),
                    ("✅ Mean attendance", fmt(grid["Attendance %"].mean(), "%")),
                    ("⚠️ Zero-day surveyors", f"{int((grid['Days logged'] == 0).sum())}"),
                ])

                st.dataframe(
                    grid,
                    column_config={
                        "Attendance %": st.column_config.ProgressColumn(
                            "Attendance %", min_value=0, max_value=100, format="%.0f%%"
                        ),
                    },
                    **FULL_WIDTH,
                )
                st.caption("1 = submitted that day, 0 = no submission.")
                download_csv(
                    grid, "⬇️ Download attendance grid (CSV)",
                    "kanpur_surveyor_attendance.csv", index=True,
                )


# ==========================================
# SURVEYOR PERFORMANCE
# ==========================================
elif view == "Surveyor Performance":
    st.subheader("🏆 Surveyor Performance")

    summary = surveyor_summary(df)
    if summary.empty:
        st.warning("⚠️ Nothing to summarise.")
    else:
        st.dataframe(
            summary,
            column_config={
                "Avg hours/day": st.column_config.NumberColumn(format="%.2f"),
                "Tickets/hour": st.column_config.NumberColumn(format="%.2f"),
            },
            **FULL_WIDTH,
        )
        download_csv(
            summary, "⬇️ Download performance table (CSV)",
            "kanpur_surveyor_performance.csv", index=True,
        )

        st.markdown("##### ⚖️ Effort vs Output")
        scatter = summary.reset_index()
        st.altair_chart(
            alt.Chart(scatter).mark_circle(size=180, opacity=0.75).encode(
                x=alt.X("Total hours:Q", title="Total hours logged"),
                y=alt.Y("Total tickets:Q", title="Tickets raised"),
                color=alt.Color("surveyor:N", legend=alt.Legend(title=None)),
                tooltip=["surveyor", "Days logged", "Total hours",
                         "Total tickets", "Tickets/hour"],
            ).properties(height=340),
            **FULL_WIDTH,
        )
        st.caption(
            "Surveyors well below the general slope are logging hours without "
            "raising proportionate tickets — worth a conversation, not a "
            "conclusion, since ward difficulty varies."
        )

        st.markdown("##### 📦 Hours per Day Spread")
        hours_df = df.dropna(subset=["hours_worked"])
        if len(hours_df) > 1:
            st.altair_chart(
                alt.Chart(hours_df).mark_boxplot(extent="min-max", color=ACCENT).encode(
                    x=alt.X("surveyor:N", title=None),
                    y=alt.Y("hours_worked:Q", title="Hours worked"),
                ).properties(height=320),
                **FULL_WIDTH,
            )
        else:
            st.caption("Needs more submissions to show a spread.")


# ==========================================
# ZONE & WARD COVERAGE
# ==========================================
elif view == "Zone & Ward Coverage":
    st.subheader("🗺️ Zone & Ward Coverage")
    st.caption(
        "A submission can list several wards, so ward rows are counted as "
        "visits. Tickets and distance are per submission and are not split "
        "across wards — they are deliberately left out of the ward table."
    )

    zone_tbl = (
        df.groupby("zone", dropna=False)
        .agg(Submissions=("surveyor", "size"),
             Surveyors=("surveyor", "nunique"),
             Days=("date", "nunique"),
             Tickets=("tickets", "sum"),
             Hours=("hours_worked", "sum"),
             Distance=("distance_km", "sum"))
        .reset_index().sort_values("Tickets", ascending=False)
    )
    st.markdown("##### 📍 By Zone")
    st.dataframe(zone_tbl, hide_index=True, **FULL_WIDTH)

    st.altair_chart(
        alt.Chart(zone_tbl.dropna(subset=["zone"])).mark_bar(color=ACCENT).encode(
            x=alt.X("Tickets:Q"),
            y=alt.Y("zone:N", sort="-x", title=None),
            tooltip=["zone", "Submissions", "Surveyors", "Tickets", "Distance"],
        ).properties(height=max(200, 32 * max(len(zone_tbl), 1))),
        **FULL_WIDTH,
    )

    st.markdown("##### 🏘️ By Ward (visits)")
    if wl.empty:
        st.warning("⚠️ No ward values could be read from these submissions.")
    else:
        ward_tbl = (
            wl.groupby(["zone", "ward"], dropna=False)
            .agg(Visits=("surveyor", "size"),
                 Surveyors=("surveyor", "nunique"),
                 Days=("date", "nunique"))
            .reset_index().sort_values("Visits", ascending=False)
        )
        st.dataframe(ward_tbl, hide_index=True, **FULL_WIDTH)
        download_csv(ward_tbl, "⬇️ Download ward coverage (CSV)", "kanpur_ward_coverage.csv")

        never_twice = ward_tbl[ward_tbl["Visits"] == 1]
        if not never_twice.empty:
            st.info(
                f"ℹ️ {len(never_twice)} wards were visited only once in this "
                f"period — useful for planning the next survey round."
            )


# ==========================================
# TRENDS
# ==========================================
elif view == "Trends":
    st.subheader("📊 Trends")

    grain = st.radio(
        "Group by", ["Month", "ISO week", "FY quarter"], horizontal=True
    )
    column = {"Month": "month", "ISO week": "iso_week", "FY quarter": "fy_quarter_label"}[grain]

    trend = (
        df.dropna(subset=[column])
        .groupby(column)
        .agg(Submissions=("surveyor", "size"),
             Surveyors=("surveyor", "nunique"),
             Days=("date", "nunique"),
             Tickets=("tickets", "sum"),
             Hours=("hours_worked", "sum"),
             Distance=("distance_km", "sum"))
        .reset_index().sort_values(column)
    )
    if trend.empty:
        st.warning("⚠️ Nothing to trend in this period.")
    else:
        trend["Tickets/day"] = (trend["Tickets"] / trend["Days"]).round(1)
        st.dataframe(trend, hide_index=True, **FULL_WIDTH)

        long = trend.melt(
            column, value_vars=["Tickets", "Hours", "Distance"],
            var_name="Measure", value_name="Value",
        )
        st.altair_chart(
            alt.Chart(long).mark_bar().encode(
                x=alt.X(f"{column}:N", title=None),
                y=alt.Y("Value:Q", title=None),
                color=alt.Color("Measure:N", legend=alt.Legend(orient="top", title=None)),
                xOffset="Measure:N",
                tooltip=[column, "Measure", "Value"],
            ).properties(height=320),
            **FULL_WIDTH,
        )

        st.markdown("##### 👥 Per-surveyor by period")
        pivot = (
            df.dropna(subset=[column])
            .pivot_table(index="surveyor", columns=column, values="tickets",
                         aggfunc="sum", fill_value=0)
        )
        st.dataframe(pivot, **FULL_WIDTH)
        st.caption("Tickets raised. Blank periods mean no submissions.")


# ==========================================
# DAILY LOG
# ==========================================
elif view == "Daily Log":
    st.subheader("📖 Daily Log")

    search = st.text_input("Search surveyor, zone, or ward")
    log = df.copy()
    if search:
        needle = search.strip().lower()
        haystack = (
            log["surveyor"].astype(str).str.lower() + " "
            + log["zone"].astype(str).str.lower() + " "
            + log["ward_raw"].astype(str).str.lower()
        )
        log = log[haystack.str.contains(needle, na=False, regex=False)]

    st.markdown(f"**{len(log)} submissions**")
    st.dataframe(
        log[[
            "date", "surveyor", "zone", "ward_raw", "start_hour", "end_hour",
            "hours_worked", "tickets", "distance_km", "submitted_at",
        ]].rename(columns={
            "date": "Date", "surveyor": "Surveyor", "zone": "Zone",
            "ward_raw": "Ward(s)", "start_hour": "Start (h)",
            "end_hour": "End (h)", "hours_worked": "Hours",
            "tickets": "Tickets", "distance_km": "Distance (km)",
            "submitted_at": "Submitted",
        }).sort_values("Date", ascending=False),
        hide_index=True,
        column_config={
            "Start (h)": st.column_config.NumberColumn(format="%.2f"),
            "End (h)": st.column_config.NumberColumn(format="%.2f"),
            "Hours": st.column_config.NumberColumn(format="%.2f"),
        },
        **FULL_WIDTH,
    )
    download_csv(log, "⬇️ Download log (CSV)", "kanpur_surveyor_log.csv")


# ==========================================
# DATA QUALITY
# ==========================================
elif view == "Data Quality":
    st.subheader("🧪 Data Quality")

    if report.is_clean:
        st.success("✅ No issues found in these submissions.")
    st.code(report.render(), language=None)

    st.markdown(
        f"Checks run: unreadable dates and times, end time before start time, "
        f"shifts over {MAX_PLAUSIBLE_HOURS} hours, implausible distances, "
        f"blank names or zones, unreadable ward values, and repeat "
        f"submissions for the same surveyor and day."
    )

    dupes = daily[daily["is_duplicate"]]
    if not dupes.empty:
        st.markdown(f"##### 🔁 Duplicate submissions ({len(dupes)})")
        st.caption(
            "Same surveyor, same date. Left in the figures unless you enable "
            "de-duplication in the sidebar, which keeps the latest by timestamp."
        )
        st.dataframe(
            dupes[["date", "surveyor", "zone", "ward_raw", "hours_worked",
                   "tickets", "distance_km", "submitted_at"]],
            hide_index=True, **FULL_WIDTH,
        )

    suspicious = daily[
        daily["date"].isna()
        | daily["hours_worked"].isna()
        | (daily["hours_worked"] > MAX_PLAUSIBLE_HOURS)
        | (daily["hours_worked"] == 0)
    ]
    if not suspicious.empty:
        st.markdown(f"##### ⚠️ Rows needing a look ({len(suspicious)})")
        st.caption(
            "Unreadable date, unreadable or zero hours, or an implausibly long "
            "shift. Nothing is dropped — these are flagged for correction at "
            "source, in the Form responses."
        )
        st.dataframe(
            suspicious[["date", "surveyor_raw", "zone", "start_hour", "end_hour",
                        "hours_worked", "tickets", "distance_km", "ward_raw"]],
            hide_index=True, **FULL_WIDTH,
        )
        download_csv(
            suspicious, "⬇️ Download flagged rows (CSV)",
            "kanpur_surveyor_flagged.csv",
        )

    if report.unparsed_wards:
        st.markdown("##### 🏘️ Unreadable ward values")
        st.dataframe(
            pd.DataFrame(
                sorted(report.unparsed_wards.items(), key=lambda kv: -kv[1]),
                columns=["Ward value", "Rows"],
            ),
            hide_index=True, **FULL_WIDTH,
        )

    st.markdown("##### 🗓️ Date reading check")
    st.caption(
        "Day-first vs month-first only diverge once a day-of-month exceeds 12, "
        "so a wrong setting can look fine for weeks. These are the earliest and "
        "latest dates as currently read."
    )
    dd = daily.dropna(subset=["date"])
    if not dd.empty:
        st.write(
            f"Currently **{'day-first (DD/MM)' if dayfirst else 'month-first (MM/DD)'}** → "
            f"earliest **{dd['date'].min():%d %b %Y}**, "
            f"latest **{dd['date'].max():%d %b %Y}**"
        )

    st.markdown("##### 💾 Parsed data")
    download_csv(daily, "⬇️ Download parsed submissions (CSV)", "kanpur_surveyor_parsed.csv")
