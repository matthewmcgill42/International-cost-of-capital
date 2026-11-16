"""
International Cost of Capital App
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data sources (fetched live, cached 24h, refresh button clears the cache):
  - Damodaran: U.S. industry unlevered betas (betas.xls)
  - Damodaran: Baa2/BBB default spread for large non-financial firms (ratings.html)
  - OECD Corporate Tax Statistics: statutory corporate income tax rates
  - Trading Economics: 10-year government bond yields

All formulas live in compute_cost_of_capital() so they can be checked
(and changed to match class notation) in one place.
"""

import io
import re
from datetime import datetime

import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
)
TE_URL = "https://tradingeconomics.com/bonds"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"

# Fallback statutory combined CIT rates (OECD Corporate Tax Statistics 2024,
# 2024 values). Used ONLY if the live OECD scrape fails; shown as "fallback".
FALLBACK_TAX = {
    "United States": 25.8, "Argentina": 35.0, "Australia": 30.0, "Austria": 23.0,
    "Belgium": 25.0, "Brazil": 34.0, "Canada": 26.1, "Chile": 27.0, "China": 25.0,
    "Colombia": 35.0, "Czech Republic": 21.0, "Denmark": 22.0, "Finland": 20.0,
    "France": 25.8, "Germany": 29.9, "Greece": 22.0, "Hong Kong": 16.5,
    "Hungary": 9.0, "India": 25.2, "Indonesia": 22.0, "Ireland": 12.5,
    "Israel": 23.0, "Italy": 27.8, "Japan": 29.7, "Mexico": 30.0,
    "Netherlands": 25.8, "New Zealand": 28.0, "Norway": 22.0, "Poland": 19.0,
    "Portugal": 31.5, "Saudi Arabia": 20.0, "Singapore": 17.0,
    "South Africa": 27.0, "South Korea": 26.4, "Spain": 25.0, "Sweden": 20.6,
    "Switzerland": 19.6, "Turkey": 25.0, "United Kingdom": 25.0,
}

# Name harmonisation between OECD and Trading Economics
NAME_MAP = {
    "Korea": "South Korea", "Republic of Korea": "South Korea",
    "Türkiye": "Turkey", "Turkiye": "Turkey",
    "Czechia": "Czech Republic", "Slovak Republic": "Slovakia",
    "Hong Kong, China": "Hong Kong", "Hong Kong (China)": "Hong Kong",
    "China (People's Republic of)": "China", "US": "United States",
    "United States of America": "United States", "UK": "United Kingdom",
    "U.S.": "United States", "USA": "United States", "Euro Area": "Euro Area",
}


def norm_country(name: str) -> str:
    name = re.sub(r"\s+", " ", str(name)).strip()
    return NAME_MAP.get(name, name)


def to_pct_number(x):
    """'4.25%' -> 4.25 ; 0.0425 stays as given by caller logic."""
    if pd.isna(x):
        return None
    s = str(x).replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r


# ---------------------------------------------------------------------------
# Data fetchers (each returns data + metadata dict with source/date/status)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_betas():
    meta = {"source": BETAS_URL, "fetched": datetime.now().strftime("%Y-%m-%d %H:%M")}
    try:
        r = get(BETAS_URL)
        raw = pd.read_excel(io.BytesIO(r.content), sheet_name=None, header=None)
        sheet = next((k for k in raw if "industry" in k.lower()), list(raw)[0])
        df = raw[sheet]
        # Data date: Damodaran puts "Date updated: ..." near the top
        top = " ".join(df.head(10).astype(str).fillna("").values.ravel())
        m = re.search(r"Date updated:?\s*([A-Za-z0-9,\- /:]+?)(?:\s+nan|\s{2,}|$)", top)
        meta["data_date"] = m.group(1).strip() if m else "see file header"
        hdr_idx = df.index[
            df.apply(lambda row: row.astype(str).str.contains("Industry Name", case=False).any(), axis=1)
        ][0]
        cols = df.iloc[hdr_idx].astype(str).str.strip()
        data = df.iloc[hdr_idx + 1:].copy()
        data.columns = cols
        ind_col = next(c for c in cols if "industry" in c.lower())
        # The plain "Unlevered beta" column (not "corrected for cash")
        ub_col = next(c for c in cols if c.lower().strip() == "unlevered beta")
        out = data[[ind_col, ub_col]].rename(columns={ind_col: "Industry", ub_col: "Unlevered beta"})
        out["Unlevered beta"] = pd.to_numeric(out["Unlevered beta"], errors="coerce")
        out = out.dropna()
        out = out[~out["Industry"].astype(str).str.contains("Total Market", case=False)]
        out["Industry"] = out["Industry"].astype(str).str.strip()
        meta["status"] = "live"
        return out.reset_index(drop=True), meta
    except Exception as e:  # noqa: BLE001
        meta.update(status=f"FAILED ({e.__class__.__name__})", data_date="n/a")
        fallback = pd.DataFrame({"Industry": [DEFAULT_INDUSTRY], "Unlevered beta": [1.20]})
        return fallback, meta


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_bbb_spread():
    meta = {"source": RATINGS_URL, "fetched": datetime.now().strftime("%Y-%m-%d %H:%M")}
    try:
        r = get(RATINGS_URL)
        text = re.sub(r"<[^>]+>", " ", r.text)
        m = re.search(r"(?:updated|Updated)[^A-Za-z0-9]*([A-Z][a-z]+ \d{4}|\d{1,2}/\d{1,2}/\d{2,4})", text)
        meta["data_date"] = m.group(1) if m else "see page"
        tables = pd.read_html(io.StringIO(r.text))
        # First table containing Baa2/BBB is the large (>$5bn) non-financial firm table
        for t in tables:
            flat = t.astype(str)
            hit = flat.apply(lambda col: col.str.contains("Baa2/BBB", regex=False)).any(axis=1)
            if hit.any():
                row = flat[hit].iloc[0].tolist()
                idx = next(i for i, v in enumerate(row) if "Baa2/BBB" in v)
                # spread is the first percentage-looking value after the rating cell
                for v in row[idx + 1:]:
                    val = to_pct_number(v)
                    if val is not None:
                        meta["status"] = "live"
                        return val / 100 if val > 0.5 else val, meta
        raise ValueError("Baa2/BBB not found")
    except Exception as e:  # noqa: BLE001
        meta.update(status=f"FAILED ({e.__class__.__name__}) – enter manually", data_date="n/a")
        return 0.0150, meta


@st.cache_data(ttl=24 * 3600, show_spinner=False)
def fetch_tax_rates():
    meta = {"source": OECD_URL, "fetched": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "data_date": "OECD Corporate Tax Statistics 2024 (2024 rates)"}
    try:
        r = get(OECD_URL)
        tables = pd.read_html(io.StringIO(r.text))
        rates = {}
        for t in tables:
            t = t.copy()
            t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
            if t.shape[1] < 2:
                continue
            cname = t.columns[0]
            rate_col = next((c for c in t.columns[1:] if "combined" in c.lower()), t.columns[-1])
            for _, row in t.iterrows():
                v = to_pct_number(row[rate_col])
                if v is not None and 0 <= v <= 60:
                    rates[norm_country(row[cname])] = v / 100
        if len(rates) < 20:
            raise ValueError("table not found in page")
        meta["status"] = "live"
        return rates, meta
    except Exception as e:  # noqa: BLE001
        meta["status"] = f"live scrape failed ({e.__class__.__name__}); using OECD 2024 fallback table"
        return {k: v / 100 for k, v in FALLBACK_TAX.items()}, meta


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_yields():
    meta = {"source": TE_URL, "fetched": datetime.now().strftime("%Y-%m-%d %H:%M")}
    try:
        r = get(TE_URL)
        if "<table" not in r.text.lower():
            raise ValueError("page returned no tables (likely bot-blocked)")
        tables = pd.read_html(io.StringIO(r.text))
        yields, dates = {}, []
        for t in tables:
            t = t.copy()
            t.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in t.columns]
            cols = list(t.columns)
            ycol = next((c for c in cols if "yield" in c.lower()), None)
            if ycol is None:
                continue
            dcol = next((c for c in cols if c.lower().strip() == "date"), None)
            # country/instrument column = first text column that isn't the yield column
            ccol = next((c for c in cols if c != ycol and t[c].astype(str).str.contains(r"[A-Za-z]{2}").mean() > 0.5),
                        None)
            if ccol is None:
                continue
            for _, row in t.iterrows():
                v = to_pct_number(row[ycol])
                if v is None or not (-2 <= v <= 60):
                    continue
                name = str(row[ccol]).strip()
                tenor = re.search(r"\b(\d+)\s*[YM]\b", name)
                if tenor and tenor.group(0).replace(" ", "").upper() != "10Y":
                    continue  # skip 2Y, 30Y, 3M etc.
                name = re.sub(r"\b\d+\s*[YM]\b", "", name).strip()
                key = norm_country(name)
                # prefer an explicit 10Y quote over a tenor-less one
                if key not in yields or tenor:
                    yields[key] = v / 100
                if dcol:
                    dates.append(str(row[dcol]))
        if "United States" not in yields:
            sample = ", ".join(list(yields)[:6]) or "none"
            raise ValueError(f"US 10Y not found; parsed names e.g. {sample}")
        meta["status"] = "live"
        meta["data_date"] = f"latest quote ({max(dates) if dates else 'intraday'})"
        return yields, meta
    except Exception as e:  # noqa: BLE001
        meta.update(status=f"FAILED ({e.__class__.__name__}: {str(e)[:120]}) – enter yields manually",
                    data_date="n/a")
        return {"United States": 0.0425}, meta


# ---------------------------------------------------------------------------
# Core calculation — keep every formula here
# ---------------------------------------------------------------------------
def compute_cost_of_capital(beta_u, tax, debt_share, erp, y_us, y_f, spread_us, spread_f_override=None):
    """
    beta_u            unlevered (asset) beta
    tax               project tax rate (decimal)
    debt_share        D/V (decimal)
    erp               equity risk premium (decimal, USD)
    y_us, y_f         10-yr government yields, US and foreign (decimal)
    spread_us         USD BBB spread over US Treasury (decimal)
    spread_f_override foreign-currency BBB spread; None => auto (parity-consistent)

    Method:
      Levered beta (Hamada):  bL = bU * [1 + (1 - t) * D/E]
      USD cost of equity:     ke$ = y_US + bL * ERP
      USD pre-tax debt:       kd$ = y_US + s$
      Conversion to foreign currency uses the government-yield differential
      (interest rate parity / international Fisher):
          (1 + k_FC) = (1 + k_USD) * (1 + y_F) / (1 + y_US)
      Auto foreign spread:    sFC = [(1 + kd$) * (1+y_F)/(1+y_US) - 1] - y_F
      WACC = E/V * ke + D/V * kd * (1 - t)
    """
    de = debt_share / (1 - debt_share) if debt_share < 1 else float("inf")
    beta_l = beta_u * (1 + (1 - tax) * de)
    fx = (1 + y_f) / (1 + y_us)

    ke_usd = y_us + beta_l * erp
    kd_usd = y_us + spread_us
    wacc_usd = (1 - debt_share) * ke_usd + debt_share * kd_usd * (1 - tax)

    ke_f = (1 + ke_usd) * fx - 1
    spread_f_auto = (1 + kd_usd) * fx - 1 - y_f
    spread_f = spread_f_auto if spread_f_override is None else spread_f_override
    kd_f = y_f + spread_f
    wacc_f = (1 - debt_share) * ke_f + debt_share * kd_f * (1 - tax)

    return {
        "levered_beta": beta_l, "de_ratio": de,
        "usd": {"rf": y_us, "spread": spread_us, "ke": ke_usd, "kd_pre": kd_usd,
                "kd_post": kd_usd * (1 - tax), "wacc": wacc_usd},
        "fc": {"rf": y_f, "spread": spread_f, "ke": ke_f, "kd_pre": kd_f,
               "kd_post": kd_f * (1 - tax), "wacc": wacc_f},
        "spread_f_auto": spread_f_auto,
    }


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="International Cost of Capital", layout="wide")
st.title("International Cost of Capital")
st.caption("Cost of equity, cost of debt and WACC for a project in USD and in a selected country's currency.")

if st.sidebar.button("🔄 Refresh data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

with st.spinner("Fetching latest data…"):
    betas_df, betas_meta = fetch_betas()
    spread_us_fetched, spread_meta = fetch_bbb_spread()
    tax_rates, tax_meta = fetch_tax_rates()
    yields, yld_meta = fetch_yields()

# ---- Selections
st.sidebar.header("Project inputs")
industries = sorted(betas_df["Industry"].unique())
ind_idx = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
industry = st.sidebar.selectbox("Industry", industries, index=ind_idx)

countries = sorted(set(yields) | set(tax_rates) | {DEFAULT_COUNTRY})
country = st.sidebar.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY))

erp = st.sidebar.slider("Equity risk premium (%)", 3.0, 10.0, 6.5, 0.1) / 100
debt_share = st.sidebar.slider("Target debt share D/V (%)", 0, 90, 30, 1) / 100
tax_choice = st.sidebar.radio("Project tax rate", ["U.S. rate", f"{country} rate"], index=0, horizontal=True)

# ---- Fetched values
beta_u_fetched = float(betas_df.loc[betas_df["Industry"] == industry, "Unlevered beta"].iloc[0])
tax_us_fetched = tax_rates.get("United States", FALLBACK_TAX["United States"] / 100)
tax_f_fetched = tax_rates.get(country)
y_us_fetched = yields.get("United States")
y_f_fetched = yields.get(country)

# ---- Manual overrides
st.sidebar.header("Manual overrides")


def override(label, fetched, key, fmt_pct=True, step=0.01):
    on = st.sidebar.checkbox(f"Override {label}", key=f"chk_{key}", value=fetched is None)
    shown = (fetched * 100 if fmt_pct else fetched) if fetched is not None else 0.0
    if on:
        v = st.sidebar.number_input(f"{label}{' (%)' if fmt_pct else ''}", value=float(round(shown, 4)),
                                    step=step, format="%.4f", key=f"num_{key}")
        return (v / 100 if fmt_pct else v), True
    st.sidebar.caption(f"Fetched {label}: {shown:.3f}{'%' if fmt_pct else ''}")
    return fetched, False


beta_u, beta_ov = override("unlevered beta", beta_u_fetched, "beta", fmt_pct=False)
tax_us, _ = override("U.S. tax rate", tax_us_fetched, "taxus")
tax_f, _ = override(f"{country} tax rate", tax_f_fetched, "taxf")
spread_us, _ = override("USD BBB spread", spread_us_fetched, "sus")
y_us, _ = override("U.S. 10-yr yield", y_us_fetched, "yus")
y_f, _ = override(f"{country} 10-yr yield", y_f_fetched, "yf")

project_tax = tax_us if tax_choice.startswith("U.S.") else tax_f
if project_tax is None or y_f is None or y_us is None:
    st.error("A required value is missing from the fetched data — turn on the matching override in the sidebar.")
    st.stop()

# Foreign spread override (auto value shown when off)
auto_preview = compute_cost_of_capital(beta_u, project_tax, debt_share, erp, y_us, y_f, spread_us)["spread_f_auto"]
sf_on = st.sidebar.checkbox(f"Override {country} BBB spread", key="chk_sf")
if sf_on:
    spread_f_override = st.sidebar.number_input(f"{country} BBB spread (%)", value=round(auto_preview * 100, 4),
                                                step=0.01, format="%.4f") / 100
else:
    spread_f_override = None
    st.sidebar.caption(f"Auto {country} BBB spread: {auto_preview * 100:.3f}%")

res = compute_cost_of_capital(beta_u, project_tax, debt_share, erp, y_us, y_f, spread_us, spread_f_override)

# ---- Results side by side
st.subheader(f"{industry} — USD vs {country}")
c1, c2 = st.columns(2)
for col, key, label in [(c1, "usd", "USD"), (c2, "fc", f"{country} currency")]:
    r = res[key]
    with col:
        st.markdown(f"### {label}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Cost of equity", f"{r['ke']:.2%}")
        m2.metric("After-tax cost of debt", f"{r['kd_post']:.2%}")
        m3.metric("WACC", f"{r['wacc']:.2%}")

pf = lambda x: f"{x:.3%}"  # noqa: E731
table = pd.DataFrame({
    "Item": ["10-yr government yield", "BBB spread", "Pre-tax cost of debt", "After-tax cost of debt",
             "Levered beta", "Equity risk premium", "Cost of equity", "Debt share D/V", "Project tax rate", "WACC"],
    "USD": [pf(res["usd"]["rf"]), pf(res["usd"]["spread"]), pf(res["usd"]["kd_pre"]), pf(res["usd"]["kd_post"]),
            f"{res['levered_beta']:.3f}", pf(erp), pf(res["usd"]["ke"]), pf(debt_share), pf(project_tax),
            pf(res["usd"]["wacc"])],
    country: [pf(res["fc"]["rf"]), pf(res["fc"]["spread"]) + (" (manual)" if sf_on else " (auto)"),
              pf(res["fc"]["kd_pre"]), pf(res["fc"]["kd_post"]), f"{res['levered_beta']:.3f}", "via parity",
              pf(res["fc"]["ke"]), pf(debt_share), pf(project_tax), pf(res["fc"]["wacc"])],
})
st.dataframe(table, hide_index=True, use_container_width=True)

csv_rows = table.copy()
csv_rows.loc[len(csv_rows)] = ["Industry", industry, industry]
csv_rows.loc[len(csv_rows)] = ["Unlevered beta", f"{beta_u:.4f}", f"{beta_u:.4f}"]
st.download_button("⬇️ Download results (CSV)", csv_rows.to_csv(index=False), "cost_of_capital_results.csv", "text/csv")

# ---- Assumptions
with st.expander("Method & main assumptions", expanded=False):
    st.markdown(f"""
- **Beta:** Damodaran U.S. industry unlevered beta, relevered with Hamada: βL = βU × [1 + (1 − t) × D/E].
  D/E = {debt_share:.0%}/{1 - debt_share:.0%} = {res['de_ratio']:.3f}.
- **USD cost of equity:** ke = U.S. 10-yr yield + βL × ERP (ERP is a USD premium).
- **USD cost of debt:** U.S. 10-yr yield + Damodaran BBB (Baa2) spread for large non-financial firms.
- **Foreign currency:** USD rates are converted with the 10-yr yield differential
  (interest rate parity / international Fisher): (1 + k_FC) = (1 + k_USD) × (1 + y_F)/(1 + y_US).
  The auto foreign BBB spread is the parity-implied foreign cost of debt minus the foreign yield.
- **Same project** tax rate, D/V and ERP are used in both currencies. Tax rate in use: **{tax_choice}** ({project_tax:.2%}).
- Government yields are treated as risk-free in their own currency (no separate sovereign default adjustment).
""")

# ---- All-industry comparison
st.subheader("All-industry comparison")
rows = []
for _, r in betas_df.iterrows():
    bu = beta_u if (beta_ov and r["Industry"] == industry) else r["Unlevered beta"]
    o = compute_cost_of_capital(bu, project_tax, debt_share, erp, y_us, y_f, spread_us, spread_f_override)
    rows.append({"Industry": r["Industry"], "Unlevered beta": round(bu, 3), "Levered beta": round(o["levered_beta"], 3),
                 "Ke USD (%)": round(o["usd"]["ke"] * 100, 2), "WACC USD (%)": round(o["usd"]["wacc"] * 100, 2),
                 f"Ke {country} (%)": round(o["fc"]["ke"] * 100, 2),
                 f"WACC {country} (%)": round(o["fc"]["wacc"] * 100, 2)})
all_df = pd.DataFrame(rows).sort_values("WACC USD (%)", ascending=False)
st.dataframe(all_df, hide_index=True, use_container_width=True, height=400)
st.bar_chart(all_df.set_index("Industry")[["WACC USD (%)", f"WACC {country} (%)"]].head(30))
st.download_button("⬇️ Download all-industry table (CSV)", all_df.to_csv(index=False),
                   "all_industry_cost_of_capital.csv", "text/csv")

# ---- Sources
st.subheader("Data sources")
st.dataframe(pd.DataFrame([
    {"Data": "Industry unlevered betas", "Source": betas_meta["source"], "Data date": betas_meta.get("data_date"),
     "Fetched": betas_meta["fetched"], "Status": betas_meta["status"]},
    {"Data": "USD BBB spread (Baa2/BBB, large firms)", "Source": spread_meta["source"],
     "Data date": spread_meta.get("data_date"), "Fetched": spread_meta["fetched"], "Status": spread_meta["status"]},
    {"Data": "Statutory corporate tax rates", "Source": tax_meta["source"], "Data date": tax_meta.get("data_date"),
     "Fetched": tax_meta["fetched"], "Status": tax_meta["status"]},
    {"Data": "10-yr government bond yields", "Source": yld_meta["source"], "Data date": yld_meta.get("data_date"),
     "Fetched": yld_meta["fetched"], "Status": yld_meta["status"]},
]), hide_index=True, use_container_width=True)
