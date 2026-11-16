# International Cost of Capital App

A Streamlit app that compares a project's cost of equity, cost of debt and WACC in **USD** and in the **currency of a selected country**.

## Data (fetched live, with source, date and a refresh button)
| Input | Source |
|---|---|
| U.S. industry unlevered betas | Damodaran, `betas.xls` |
| USD BBB (Baa2) spread, large non-financial firms | Damodaran, `ratings.html` |
| Statutory corporate income tax rates | OECD Corporate Tax Statistics 2024 (falls back to a built-in OECD 2024 table if the page can't be scraped) |
| 10-year government bond yields | Trading Economics `/bonds` (scraped, no API key) |

Every fetched value can be overridden in the sidebar. The foreign-currency BBB spread is calculated automatically unless its override is switched on.

## Method
- Levered beta: βL = βU × [1 + (1 − t) × D/E]
- USD cost of equity: ke = y_US + βL × ERP
- USD pre-tax cost of debt: kd = y_US + BBB spread
- Foreign currency: (1 + k_FC) = (1 + k_USD) × (1 + y_F) / (1 + y_US)
- Auto foreign spread = parity-implied foreign cost of debt − y_F
- WACC = E/V × ke + D/V × kd × (1 − t), same tax rate, D/V and ERP in both currencies

All formulas are in `compute_cost_of_capital()` in `app.py`.

## Defaults
Software (System & Application), United States, U.S. tax rate, 6.5% ERP, 30% debt.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Verification checks
1. Equal government yields (override off for foreign spread) → USD and foreign results are identical.
2. Debt share = 0% → WACC equals cost of equity.
3. Overriding the foreign spread leaves USD results unchanged.
