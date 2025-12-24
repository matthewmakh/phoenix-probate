import os
import re
import pandas as pd
import streamlit as st
import usaddress

DEFAULT_CSV = os.path.join("data", "probate_records.csv")

st.set_page_config(page_title="Probate PDF Extracts", layout="wide")
st.title("Probate Records Dashboard")

csv_path = st.sidebar.text_input("CSV path", DEFAULT_CSV)
reload = st.sidebar.button("Reload CSV")

def _get_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


@st.cache_data(show_spinner=False)
def load_data(path: str, mtime: float):
    if not os.path.exists(path):
        return pd.DataFrame()
    df = pd.read_csv(path)
    
    # Normalize column names - handle both old and new formats
    # Map: CSV column name -> internal name we'll use
    column_map = {
        "County": "county",
        "File number": "file_number",
        "Date of death": "date_of_death",
        "Decedent name": "decedent_name",
        "Decedent address": "decedent_address",
        "Executor/administrator name": "executor_name",
        "Executor/administrator phone": "executor_phone",
        "Executor/administrator address": "executor_address",
        "Executor/administrator email": "executor_email",
    }
    
    # Rename columns if they exist
    rename_dict = {old: new for old, new in column_map.items() if old in df.columns}
    df = df.rename(columns=rename_dict)
    
    # Parse dates if present
    for col in ("date_of_death", "run_at", "decedent_dod", "entry_date"):
        if col in df.columns:
            try:
                df[col] = pd.to_datetime(df[col], errors="coerce")
            except Exception:
                pass
    
    # Normalize phone/strings
    for col in ("executor_phone", "executor_email", "file_number", "county"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip()
    
    # Create a simple unique id
    if "file_number" in df.columns:
        df["record_id"] = df["file_number"].fillna("")
    else:
        df["record_id"] = df.index.astype(str)
    
    return df

if reload:
    load_data.clear()

csv_mtime = _get_mtime(csv_path)
df = load_data(csv_path, csv_mtime)

if df.empty:
    st.info("No data found. Run the extractor to populate the CSV first.")
    st.stop()

with st.sidebar:
    # County filter
    counties = sorted([c for c in df.get("county", pd.Series([])).dropna().unique() if c and c != "nan"])
    county_choice = st.selectbox("County", ["All"] + counties, index=0)
    
    # Name search
    name_query = st.text_input("Search Name (Decedent or Executor)")
    
    # Hide apartments filter
    hide_apartments = st.checkbox("Hide apartments (Apt/Unit/Suite/#)", value=False)

fdf = df.copy()

# Apply county filter
if county_choice and county_choice != "All":
    fdf = fdf[fdf["county"] == county_choice]

# Apply name search filter
if name_query:
    nq = name_query.strip().lower()
    cols = [c for c in ["decedent_name", "executor_name"] if c in fdf.columns]
    if cols:
        mask = pd.Series([False] * len(fdf), index=fdf.index)
        for c in cols:
            mask = mask | fdf[c].fillna("").str.lower().str.contains(nq, regex=False)
        fdf = fdf[mask]

# Optional filter: hide addresses that look like apartments/units
def _looks_like_apartment(s: str) -> bool:
    """Use usaddress library to detect if address contains a unit/apartment."""
    if not s:
        return False
    s = str(s)
    if s == "nan" or s.strip() == "":
        return False
    
    try:
        parsed, _ = usaddress.tag(s)
        # Check for occupancy indicators (Apt, Suite, Unit, Floor, etc.)
        return "OccupancyType" in parsed or "OccupancyIdentifier" in parsed
    except usaddress.RepeatedLabelError:
        # Fallback to regex if usaddress can't parse
        low = s.lower()
        if re.search(r"\b(apt\.?|apartment|unit|suite|ste\.?|fl\.?|floor|rm\.?|room)\b", low):
            return True
        return False
    except Exception:
        return False

if hide_apartments:
    # Check both decedent and executor addresses
    for addr_col in ["decedent_address", "executor_address"]:
        if addr_col in fdf.columns:
            fdf = fdf[~fdf[addr_col].fillna("").map(_looks_like_apartment)]

st.subheader("Overview")
left, mid, right = st.columns(3)
with left:
    st.metric("Total Records", len(df))
with mid:
    st.metric("Filtered Records", len(fdf))
with right:
    st.metric("Unique File Numbers", fdf.get("file_number", pd.Series([])).nunique())

st.subheader("Records Table")

# Display columns in order
display_cols = [
    c for c in [
        "county",
        "file_number",
        "decedent_name",
        "date_of_death",
        "decedent_address",
        "executor_name",
        "executor_phone",
        "executor_address",
        "executor_email",
    ] if c in fdf.columns
]

# Friendly column names for display
column_labels = {
    "county": "County",
    "file_number": "File Number",
    "decedent_name": "Decedent Name",
    "date_of_death": "Date of Death",
    "decedent_address": "Decedent Address",
    "executor_name": "Executor/Admin Name",
    "executor_phone": "Phone",
    "executor_address": "Executor Address",
    "executor_email": "Email",
}

df_view = fdf[display_cols] if display_cols else fdf

# Sort by county then file number
sort_by = [c for c in ["county", "file_number"] if c in df_view.columns]
if sort_by:
    try:
        df_view = df_view.sort_values(by=sort_by, ascending=True)
    except Exception:
        pass

# Rename columns for display
df_view = df_view.rename(columns=column_labels)

st.dataframe(df_view, height=520, use_container_width=True)

st.subheader("Breakdown by County")
if "county" in fdf.columns and not fdf.empty:
    county_counts = fdf["county"].value_counts().sort_values(ascending=False)
    st.bar_chart(county_counts)

st.caption("Data source: CSV generated by probate_scraper.py")
