# Public deployment

The included multi-stage Dockerfile builds React/Vite and runs FastAPI. It serves the compiled frontend and API from the same origin, allowing browser sessions without cross-origin cookie configuration. Frontend and backend source remain separate. PostgreSQL keeps original uploads, revisions, and generated documents across container restarts. No public instance has been provisioned from this workspace.

## Render Blueprint (recommended)

1. Create a Git repository containing the contents of the `datapilot-ai` folder. Commit `Dockerfile` and `render.yaml` at the repository root. Do not commit local databases or `.env` files.
2. Sign in to Render in your own account, authorize your Git provider, and select **New → Blueprint**. Choose the repository and branch.
3. Review and approve the paid web service and database plans in `render.yaml`, or pick plans with adequate memory/storage for your project. Render injects the private PostgreSQL connection string via `fromDatabase`. Keep public database network access disabled.
4. Once deployed, open the HTTPS service URL shown in the dashboard. Verify `/api/health` returns `{"status":"ok"}`. Register, upload a small file, generate a report, redeploy, and verify the report persists. The Render URL becomes your public site URL. Add a custom domain in service settings if desired.
5. Optional sample mode: set `ENABLE_DEMO=true` at runtime to allow the **Try sample workspace** button. To also open a sample automatically, change the Docker build argument `VITE_AUTO_DEMO` to `true` (or provide the build arg explicitly) and rebuild.

## Docker on a VM or another managed host

```bash
docker build -t datapilot-ai .
# Local image smoke test with persistent SQLite
mkdir -p ./pilot-state
# Ensure the container user (UID 10001) can write to the mounted directory.
docker volume create datapilot-state
docker run --rm -p 10000:10000 -v datapilot-state:/app/data -e COOKIE_SECURE=false datapilot-ai
```

For a public deployment, supply a managed PostgreSQL `DATABASE_URL` via your provider's secret configuration, set `COOKIE_SECURE=true`, allow HTTPS only at the proxy, and expose the provider's `$PORT` (default `10000`). Do not put an actual database password in a committed file or in Docker build args. If using a separate static frontend host, configure it or a gateway to proxy **all** `/api/*` requests to FastAPI unchanged, including cookies and request bodies. Check host-specific upload and timeout limits before choosing that setup.

| Variable | Meaning |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy database URL. `postgres://` and `postgresql://` are normalized to psycopg. |
| `COOKIE_SECURE` | `true` for public HTTPS; `auto` for development and the Arena preview; `false` only for local HTTP. |
| `ENABLE_DEMO` | Set to `false` to disable issuing new sample workspaces. |
| `VITE_AUTO_DEMO` | Build-time string `false` starts the UI at sign-in when there is no session. |
| `PORT` | HTTP port used by Docker. |
| `STATIC_DIR` | Compiled frontend directory, configured automatically by Docker. |
| `TRUST_PROXY_IP` | Keep `false` unless your trusted edge normalizes the forwarded client-IP header; otherwise rate limiting sees the proxy IP. |

For institutional use, configure database backups and a data retention policy, review access restrictions, monitor resource usage, and route traffic through a gateway with shared rate limiting. Uploaded data and PDFs are not written to persistent local disk, but database growth from revisions/reports should be monitored.
