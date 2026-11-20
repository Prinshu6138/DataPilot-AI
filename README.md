# DataPilot AI

**An Intelligent Data Analysis and Automated Reporting System** — an MCA web application. The React frontend and FastAPI backend are maintained separately; they can be served behind one public hostname. This implementation uses a browser interface instead of the Tkinter desktop UI described in the earlier synopsis.

## Quick start

Requirements: Python 3.12 or 3.13 and Node.js 20.19+ or 22.12+. Run from the extracted `datapilot-ai` directory:

```bash
# Terminal 1
cd backend
python -m venv .venv
source .venv/bin/activate              # PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

```bash
# Terminal 2
cd frontend
npm ci
npm run dev
```

Visit `http://localhost:5173`. Vite proxies `/api` to port 8000. The default isolated sample workspace includes four synthetic datasets and two generated reports. Use the avatar menu to create your own account, or set `ENABLE_DEMO=false` before launching the backend to start at sign-in. Sample workspaces expire after 24 hours and are pruned when a new sample workspace is created. Files do not transfer to a registered account.

### Take a dataset from upload to report

1. Select **New analysis**, drop a CSV/Excel file such as `examples/sales-demo.csv`, and click **Upload and analyze**.
2. Review records, search/filter, and open **Column profile** or **Statistics**.
3. Open **Data Cleaning**, preview transformations, and apply only after reviewing the changes. Undo or reset when needed. The original upload is retained until dataset deletion.
4. Explore **AI Insights** and the interactive **Visualizations** chart builder.
5. Generate a report in **Reports**. Open the saved report for details, download PDF or editable Word, or use **Open / Print** and print from your browser's PDF viewer. Download processed data separately as CSV or XLSX.

## Architecture and data handling

| Path | Responsibility |
| --- | --- |
| `frontend/src/main.jsx`, `styles.css` | Application shell, routes, authentication views, responsive design |
| `frontend/src/Dashboard.jsx`, `Datasets.jsx`, `Explore.jsx`, `Workspace.jsx` | Dashboards, uploads, record browsing, cleaning, insights, charts, reports, history |
| `backend/app/main.py` | API routes, request validation, authentication, access control, workflows |
| `backend/app/analytics.py` | Parsing, profiling, cleaning, descriptive statistics, insight rules and chart aggregation |
| `backend/app/store.py` | SQLAlchemy schema and SQLite/PostgreSQL connection |
| `backend/app/reports.py` | Matplotlib report figure and PDF/Word rendering |

Tables hold users, revocable sessions, datasets, analysis runs, cleaning revisions, and generated reports. Argon2id protects passwords. Sessions use random tokens in HTTP-only cookies; only the token digest is stored. State-changing API calls require an app-specific header and the API does not enable cross-origin access. Reads, exports, and mutations are owner-scoped. Uploads have byte, row, and column limits, file signature checks for Excel, and expanded ZIP limits for XLSX; temporary multipart files are closed by request cleanup. Spreadsheet exports protect formula-like text. Original uploads, working copies, revision snapshots and reports are database blobs, so redeploying the app does not delete them. Deleting a dataset removes its analysis/cleaning history but intentionally preserves previously generated reports.

The insight engine uses actual measurements: ranges, missingness, duplicates, category averages and counts, period comparisons, IQR outliers, and Pearson correlations. It does not call an LLM or require an API key. Recommendations are labeled separately; there is no forecasting or causal inference. Cell completeness is the share of non-missing cells, not a comprehensive quality score.

API docs: `http://localhost:8000/docs`. See [DEPLOYMENT.md](DEPLOYMENT.md) for Docker, public hosting, PostgreSQL and environment variables.

## Tests

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

To run the browser tests, start both local servers with sample workspaces enabled and use a disposable local database. Run:

```bash
cd frontend
npm ci
npx playwright install chromium        # Linux CI: add --with-deps if needed
npm run test:e2e
npm run build
```

See [TESTING.md](TESTING.md) for verified results and scope.

## Current limits

- 8 MB per upload, 50,000 rows, 80 columns, 50 datasets and 60 reports per workspace. Excel imports the first sheet. CSV requires UTF-8 (BOM accepted). Date inference recognizes ISO-formatted text and native Excel dates; use Data Cleaning to convert other date text. Reports choose a suitable built-in chart rather than reproducing every custom chart configuration.
- Statistical results are descriptive; numeric identifiers may be inferred as numbers. Excel formulas are not executed by the app; imports use stored cell results when available. Chart points/categories may be capped for usability. The correlation heatmap is interactive in-app via its displayed matrix, but PNG export applies to the other five chart types.
- No email verification, password recovery, team access, billing, scheduled reporting, or background job queue. The database creates an initial schema on startup; add managed schema migrations and shared rate limiting before multi-instance production use. Back up PostgreSQL and set your own retention policy.
"# DataPilot-AI" 
"# DataPilot-AI" 
