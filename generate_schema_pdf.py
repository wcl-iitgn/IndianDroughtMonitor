#!/usr/bin/env python3
# =============================================================================
# generate_schema_pdf.py  --  India Drought Monitor (WCL, IIT Gandhinagar)
# -----------------------------------------------------------------------------
# Builds data/IDM_Database_Schema.pdf: a detailed reference for the three
# in-browser AlaSQL tables exposed on the "Query the Data" page (data-query.html)
# -- table descriptions, every column with type and meaning, data sources, and
# example queries. Pure-Python (reportlab); English technical reference.
#
#   pip install reportlab
#   python3 generate_schema_pdf.py
# =============================================================================

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                                Image, HRFlowable)

REPO = Path(__file__).resolve().parent
OUT = REPO / "data" / "IDM_Database_Schema.pdf"

TITLE_BLUE = colors.HexColor("#0F2F4A")
MUTED = colors.HexColor("#6B635E")
HEAD_BG = colors.HexColor("#0F2F4A")
ROW_ALT = colors.HexColor("#F3F1EF")
BORDER = colors.HexColor("#C9C4C0")

TABLES = [
    {
        "name": "drought_timeseries",
        "desc": "National weekly drought area derived from the Combined Drought Index (CDI). "
                "One row per week, from July 2021 to the present.",
        "cols": [
            ("date", "TEXT", "Week-ending date as 'YYYY-MM-DD'. The latest week is MAX(date)."),
            ("year, month, day", "INTEGER", "Numeric components of the week-ending date."),
            ("normal_pct", "FLOAT", "Percent of India's area in NO drought that week."),
            ("d0_pct", "FLOAT", "Percent of area in D0 (Abnormally Dry) or worse \u2014 cumulative."),
            ("d1_pct", "FLOAT", "Percent of area in D1 (Moderate) or worse \u2014 cumulative."),
            ("d2_pct", "FLOAT", "Percent of area in D2 (Severe) or worse \u2014 cumulative."),
            ("d3_pct", "FLOAT", "Percent of area in D3 (Extreme) or worse \u2014 cumulative."),
            ("d4_pct", "FLOAT", "Percent of area in D4 (Exceptional) \u2014 cumulative."),
        ],
        "note": "d0_pct\u2026d4_pct are cumulative ('or worse'); normal_pct + d0_pct = 100. "
                "'In drought' = d0_pct. Source: India_Drought_Area_Timeseries.txt.",
    },
    {
        "name": "drought_state_latest",
        "desc": "Per-state / Union-Territory drought breakdown for the most recent week only.",
        "cols": [
            ("state", "TEXT", "State or Union Territory name."),
            ("none_pct", "FLOAT", "Percent of the state in no drought."),
            ("d0_pct \u2026 d4_pct", "FLOAT", "Percent of the state in EXACTLY that class (not cumulative)."),
            ("drought_pct", "FLOAT", "Percent of the state in ANY drought (= 100 \u2212 none_pct)."),
        ],
        "note": "Per-class shares (not cumulative). Each row is ONE state \u2014 never a national total. "
                "Computed live from the latest CDI grid and the state mask.",
    },
    {
        "name": "hydro_outlook",
        "desc": "India Hydrological Outlook national means for the latest month, with recent history "
                "and a one-month-ahead forecast.",
        "cols": [
            ("parameter", "TEXT", "'Rainfall', 'Surface Air Temperature', 'Relative Wetness "
                                  "(Soil Moisture)', 'Total Runoff', or 'Evapotranspiration'."),
            ("kind", "TEXT", "'percentile' (0\u2013100, ~50 normal) or 'anomaly_degC' / 'anomaly_pct' "
                             "(0 = normal, negative = below normal)."),
            ("current_month", "FLOAT", "National mean for the latest observed month."),
            ("forecast_month", "FLOAT", "National mean for the one-month-ahead forecast."),
            ("prev_1 \u2026 prev_4", "FLOAT", "The four months before the current month."),
            ("last_year_same_month", "FLOAT", "Same calendar month, previous year."),
            ("driest, wettest", "FLOAT", "Historically most extreme analogue months."),
        ],
        "note": "Only Rainfall is a 0\u2013100 percentile; the others are anomalies "
                "(negative = below normal). Source: the monthly Hydrological Outlook means.",
    },
    {
        "name": "drought_district_latest",
        "desc": "Per-district drought breakdown for the most recent week. Loaded with the assistant "
                "data; district boundaries themselves load only when a state is clicked on the map.",
        "cols": [
            ("district", "TEXT", "District name."),
            ("state", "TEXT", "State or Union Territory the district belongs to."),
            ("state_id", "INTEGER", "Numeric state id (matches the state layer)."),
            ("none_pct", "FLOAT", "Percent of the district in no drought."),
            ("d0_pct \u2026 d4_pct", "FLOAT", "Percent of the district in EXACTLY that class (not cumulative)."),
            ("drought_pct", "FLOAT", "Percent of the district in ANY drought (= 100 \u2212 none_pct)."),
        ],
        "note": "Each row is ONE district \u2014 never a state or national total. Computed by "
                "point-in-polygon of the district boundaries against the latest CDI grid.",
    },
]

EXAMPLES = [
    ("Current national drought",
     "SELECT date, normal_pct, d0_pct FROM drought_timeseries ORDER BY date DESC LIMIT 1"),
    ("Five worst-affected states",
     "SELECT state, drought_pct, d3_pct, d4_pct FROM drought_state_latest ORDER BY drought_pct DESC LIMIT 5"),
    ("Week-on-week trend (last 8 weeks)",
     "SELECT date, d0_pct, d2_pct FROM drought_timeseries ORDER BY date DESC LIMIT 8"),
    ("States with >5% in extreme+ (D3+D4)",
     "SELECT state, (d3_pct + d4_pct) AS d3plus FROM drought_state_latest WHERE (d3_pct + d4_pct) > 5 ORDER BY d3plus DESC"),
    ("Rainfall outlook detail",
     "SELECT * FROM hydro_outlook WHERE parameter = 'Rainfall'"),
    ("Worst-affected districts in a state",
     "SELECT district, drought_pct, d2_pct, d3_pct FROM drought_district_latest WHERE state = 'Maharashtra' ORDER BY drought_pct DESC LIMIT 10"),
]


def build():
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontName="Helvetica",
                          fontSize=9.5, leading=13.5, textColor=colors.HexColor("#1A1A1A"))
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontName="Helvetica-Bold",
                        fontSize=20, textColor=TITLE_BLUE, spaceAfter=2, alignment=0)
    sub = ParagraphStyle("sub", parent=body, textColor=MUTED, fontSize=10.5)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontName="Helvetica-Bold",
                        fontSize=13, textColor=TITLE_BLUE, spaceBefore=12, spaceAfter=4)
    mono = ParagraphStyle("mono", parent=body, fontName="Courier", fontSize=8.6, leading=11.5)
    cell = ParagraphStyle("cell", parent=body, fontSize=8.8, leading=11.5)
    cellb = ParagraphStyle("cellb", parent=cell, fontName="Courier", fontSize=8.6)
    note = ParagraphStyle("note", parent=body, fontSize=8.6, textColor=MUTED, leading=11.5)

    doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                            leftMargin=1.8 * cm, rightMargin=1.8 * cm,
                            topMargin=1.6 * cm, bottomMargin=1.6 * cm,
                            title="IDM Database Schema", author="Water and Climate Lab, IIT Gandhinagar")
    story = []

    logo = REPO / "assets" / "logos" / "wcl.png"
    if logo.exists():
        im = Image(str(logo)); im._restrictSize(3.6 * cm, 1.7 * cm)
        im.hAlign = "LEFT"; story.append(im); story.append(Spacer(1, 4))

    story.append(Paragraph("India Drought Monitor \u2014 Database &amp; Query Schema", h1))
    story.append(Paragraph("Reference for the read-only AlaSQL tables on the &ldquo;Query the Data&rdquo; page. "
                           "All tables are loaded and queried entirely in the browser; only SELECT is allowed.", sub))
    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1, color=BORDER))
    story.append(Spacer(1, 4))

    for t in TABLES:
        story.append(Paragraph(t["name"], h2))
        story.append(Paragraph(t["desc"], body))
        story.append(Spacer(1, 4))
        data = [[Paragraph("<b>Column</b>", ParagraphStyle("ch", parent=cell, textColor=colors.white)),
                 Paragraph("<b>Type</b>", ParagraphStyle("ch2", parent=cell, textColor=colors.white)),
                 Paragraph("<b>Description</b>", ParagraphStyle("ch3", parent=cell, textColor=colors.white))]]
        for name, typ, desc in t["cols"]:
            data.append([Paragraph(name, cellb), Paragraph(typ, cell), Paragraph(desc, cell)])
        tbl = Table(data, colWidths=[3.7 * cm, 2.6 * cm, 10.0 * cm], hAlign="LEFT")
        st = [("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
              ("GRID", (0, 0), (-1, -1), 0.4, BORDER),
              ("VALIGN", (0, 0), (-1, -1), "TOP"),
              ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
              ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]
        for i in range(1, len(data)):
            if i % 2 == 0:
                st.append(("BACKGROUND", (0, i), (-1, i), ROW_ALT))
        tbl.setStyle(TableStyle(st))
        story.append(tbl)
        story.append(Spacer(1, 3))
        story.append(Paragraph("Note: " + t["note"], note))

    story.append(Paragraph("Example queries", h2))
    for label, sql in EXAMPLES:
        story.append(Paragraph("\u2022 " + label, body))
        story.append(Paragraph(sql.replace("<", "&lt;").replace(">", "&gt;"), mono))
        story.append(Spacer(1, 4))

    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.7, color=BORDER))
    story.append(Spacer(1, 4))
    story.append(Paragraph("\u00a9 2026 Water and Climate Lab \u00b7 Indian Institute of Technology Gandhinagar "
                           "\u00b7 For research and demonstration purposes.", note))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.build(story)
    print("wrote", OUT.relative_to(REPO))


if __name__ == "__main__":
    build()
