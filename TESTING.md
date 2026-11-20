# Verification results

Verified in the workspace on 20 November 2026 with Python 3.13, Node.js 20, and Playwright Chromium.

| Test run | Result | Coverage |
| --- | --- | --- |
| `python -m pytest -q` on SQLite | 16 passed | Registration and Argon2id, session revocation, login, request-header enforcement, malformed and oversized files, CSV/XLS/XLSX, record filtering, dataset profiling, duplicate/missing detection, reversible cleaning, statistics, numerical insights, all six chart data routes, PDF/DOCX, exports, report history, deletion, account isolation, formula-safe export |
| Same test suite on PostgreSQL 17 | 16 passed | Same workflows on PostgreSQL, including foreign-key handling and persistence of reports after dataset deletion |
| `npm run test:e2e` on Chromium | 2 passed | Dashboard and sample workspace, global search, mobile navigation, registration, upload, filtering and stats, cleaning, Excel download, insights, charts and PNG export, PDF and Word downloads with file signatures, history, logout and sign-in |
| `npm run build` | Passed | Production React/Vite bundle |
| FastAPI production static-bundle smoke check | Passed | Served index and referenced bundles, app icon, CSP header, `/api/health`, and dashboard authentication |

`PILOT_TEST_DATABASE_URL` can target a **fresh, disposable** PostgreSQL database; test records are written to it. Omit the variable to use a temporary SQLite database. The backend suite emits a non-blocking Starlette warning about its current HTTPX test adapter. Cloud provisioning and a Docker image build are not part of the verification above; the production bundle was built and served directly by FastAPI in the sandbox. Firefox/Safari and physical printing were not tested.
