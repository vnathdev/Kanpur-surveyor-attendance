# Kanpur Surveyor Attendance & Performance Dashboard

Reads the published Google Form response sheet for field surveyor daily logs.

## Run

```bash
pip install -r requirements.txt
streamlit run surveyor_app.py
```

Same dependencies as the grievance dashboard, so if that already runs, this
needs nothing new.

## Files

| File | Purpose |
|---|---|
| `surveyor_app.py` | Streamlit UI: navigation, filters, views |
| `surveyor_normalize.py` | Sheet parsing, ward splitting, validation |
| `surveyor_attendance.csv` | Optional local copy of the responses (see below) |

## Where the data comes from

Pick one in the sidebar:

1. **Published sheet (live)** — needs outbound `docs.google.com` access
2. **Local file** — `surveyor_attendance.csv` or `.xlsx` beside `surveyor_app.py`
3. **Upload a file** — one-off, no folder access needed

If the machine blocks `docs.google.com` (as it does for the grievance
dashboard's definitions sheet), option 1 fails with a message pointing at the
other two. Download the response sheet as CSV and use option 2 for a
permanent fix.

## Views

| View | Contents |
|---|---|
| Overview | Headline metrics, daily submission trend, tickets by surveyor |
| Attendance Calendar | Surveyor × date grid, attendance % against an explicit working-day window |
| Surveyor Performance | Per-surveyor table, effort-vs-output scatter, hours-per-day spread |
| Zone & Ward Coverage | Zone table and chart, ward visit counts, single-visit wards |
| Trends | Month / ISO week / FY quarter aggregation, per-surveyor pivot |
| Daily Log | Searchable submission log with CSV export |
| Data Quality | Validation report, duplicates, flagged rows, date-reading check |

Filters apply across all views: period (all time, date range, month, ISO
week, FY quarter), surveyor, and zone. FY quarters follow the Indian
financial year — Q1 is April to June.

## Two things to understand before reading the numbers

**Attendance is not in this sheet.** The form records who *did* submit; a
missing row is only absence if that person was expected to work that day. So
the Attendance Calendar asks you for the working-day window and, optionally,
a roster — it does not infer either. Sunday is excluded by default; public
holidays are not in the data and need the window narrowed by hand.

**Ward is multi-valued.** `"16, 20"` means one visit covering two wards. Ward
rows are therefore counted as *visits*, and tickets and distance are
deliberately excluded from the ward table — they are recorded per submission,
not per ward, so summing them there would double-count.

## Date parsing

`09/09/2026` is ambiguous. Day-first and month-first only diverge once a
day-of-month exceeds 12, so a wrong setting can look correct for weeks and
then silently reorder your months. The sidebar has a day-first toggle (on by
default) and the Data Quality view shows the earliest and latest dates as
currently read, so the setting can be checked against reality.

## Duplicate submissions

Surveyors sometimes submit the form twice for one day. These are left in the
figures by default and listed in Data Quality; the sidebar has a toggle to
keep only the latest submission per surveyor per day, by timestamp.

## Validation checks

Unreadable dates and times, end time before start time, shifts over 14 hours,
distances over 200 km, blank names or zones, unreadable ward values, and
repeat submissions for the same surveyor and day. Nothing is dropped — rows
are flagged for correction at source in the Form responses.
