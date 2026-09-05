# HANDS_ON.md — Setup, Run, and Troubleshooting

Full walkthrough for getting MerchantAI running locally on Windows
(PowerShell or Git Bash), from an empty machine to a working demo.

## Prerequisites

- **Docker Desktop** — installed and running (check the whale icon in
  your system tray; it must be steady, not animating, before Docker
  commands will work)
- **Python 3.11+**
- **uv** (fast Python package manager) — install with:
  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```
  (close and reopen your terminal after installing, then verify with
  `uv --version`)
- **A free Groq API key** — get one at https://console.groq.com (no
  credit card required; free tier is enough for this project)

## 1. Start the database

From the project root (the folder containing `docker-compose.yml`):

```bash
docker compose up -d
docker ps
```

Wait ~10 seconds, then re-run `docker ps` — you want to see
`merchant-ai-postgres` with status `Up ... (healthy)`, not just `Up`.

## 2. Set up the Python environment

From `backend/`:

```bash
uv venv
source .venv/Scripts/activate      # Git Bash
# or: .venv\Scripts\activate       # PowerShell
uv pip install -r requirements.txt
```

## 3. Configure environment variables

```bash
cp .env.example .env
```

Then edit `.env` directly (avoid Notepad — see Troubleshooting #2) and set:

```
DATABASE_URL=postgresql://merchant_ai:merchant_ai_dev_password@localhost:5432/merchant_ai
GROQ_API_KEY=your_real_key_here
GROQ_MODEL=openai/gpt-oss-120b
```

Verify it loaded correctly:

```bash
python -c "from app.config import settings; print(repr(settings.database_url)); print('groq key set:', bool(settings.groq_api_key))"
```

Expect the real Postgres URL and `groq key set: True`. If you see a
SQLite URL or `False`, see Troubleshooting #1–#3 below.

## 4. Run migrations and seed data

```bash
export PYTHONPATH=.        # Git Bash; PowerShell: $env:PYTHONPATH="."
alembic upgrade head
python -m app.db.seed
```

Expect: `Seeded merchant '...' — Products: 29  Customers: 40  Orders: ~1200`

## 5. Run the backend

```bash
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000/docs` — you should see the full Swagger UI
with all routes (agent, approvals, audit, campaigns, policies, buyer).

## 6. Run the frontend (separate terminal)

```bash
cd backend
source .venv/Scripts/activate
streamlit run dashboard.py
```

Opens at `http://localhost:8501`.

## 7. Get your merchant_id (for API testing)

```bash
python -c "
from app.db.base import SessionLocal
from app.models.commerce import Merchant
db = SessionLocal()
m = db.query(Merchant).first()
print('merchant_id:', m.id)
db.close()
"
```

## 8. Test scenarios

Via Streamlit UI, or via Swagger UI's `POST /api/agent/runs` with body
`{"merchant_id": "...", "request_text": "..."}`:

| Try this | Expect |
|---|---|
| "How can I increase revenue this week?" | `status: completed`, grounded recommendation, no approval |
| "What should I cross-sell to laptop buyers?" | Bundle recommendation citing real co-purchase data |
| "Create a 10% offer for laptop buyers" | `status: awaiting_approval` → approve it → check `/api/campaigns` |
| "Create a 50% discount on wearables" | `status: blocked`, no approval created |

Check `GET /api/audit-logs` for any `run_id` to see the full decision
trail.

---

## Troubleshooting — every real error we've hit and its fix

### 1. `psycopg2.OperationalError: connection to server ... failed: Connection refused`
Postgres container isn't running (it doesn't survive a machine
sleep/restart automatically). Fix, every time:
```bash
docker compose up -d
docker ps    # confirm "healthy" before retrying anything
```

### 2. Config loads SQLite instead of Postgres / `groq key set: False`
Almost always means `.env` is empty, malformed, or has a UTF-8 BOM
(commonly from Notepad). Diagnose:
```bash
cat .env          # is it actually populated?
```
Fix — rewrite it cleanly via bash heredoc (never Notepad, never
PowerShell `Set-Content -Encoding UTF8`, which can still add a BOM):
```bash
cat > .env << 'ENVEOF'
DATABASE_URL=postgresql://merchant_ai:merchant_ai_dev_password@localhost:5432/merchant_ai
GROQ_API_KEY=your_real_key_here
GROQ_MODEL=openai/gpt-oss-120b
RAZORPAY_KEY_ID=
RAZORPAY_KEY_SECRET=
ENVIRONMENT=development
ENVEOF
```

### 3. `ModuleNotFoundError: No module named 'app'`
You're not in the `backend/` directory. Run `pwd` (bash) or check your
prompt — it must end in `...merchant-ai\backend`. `cd` back in and
reactivate the venv.

### 4. Commands not recognized (`Set-Content: command not found`, etc.)
You're in the wrong shell. PowerShell cmdlets (`Set-Content`,
`Copy-Item`) don't work in Git Bash, and bash heredocs (`cat << EOF`)
don't work the same way in PowerShell. Check your prompt: `PS ...>` is
PowerShell, `$` with `MINGW64` is Git Bash. Use matching commands.

### 5. `psycopg2-binary` fails to build (`Microsoft Visual C++ 14.0 required`)
The pinned version doesn't have a prebuilt wheel for your Python version.
Use `psycopg2-binary==2.9.10` or newer in `requirements.txt`.

### 6. Groq: `model_not_found` / `404`
The model name changed or moved to Enterprise-only tier. Check
`https://console.groq.com/docs/models` for currently available models
and update `GROQ_MODEL` in `.env`.

### 7. Groq: `Failed to validate JSON` / empty `failed_generation`
The model spent its entire token budget on internal reasoning (this
happens with reasoning-capable models like `gpt-oss-120b`) and had
nothing left for the actual answer. Fix: increase `max_tokens` and pass
`reasoning_effort="low"` in the LLM call (already done in
`llm_service.py` / `nodes.py` — if you see this again, the token budget
may still be too tight for a particular prompt).

### 8. `Completions.create() got an unexpected keyword argument 'reasoning_effort'`
Your installed `groq` SDK is too old. Upgrade:
```bash
uv pip install --upgrade groq==1.7.0
```

### 9. Swagger UI `422 Unprocessable Content`
Check the exact field names the schema expects (visible in the response
body's `"loc"` field) — don't assume field names like `message` match;
confirm against the actual Pydantic schema shown in `/docs`.

### 10. VS Code: "The name X is not valid as a file or folder name"
Usually a trailing space typed into the New File input box, or you
clicked New Folder instead of New File. Skip the UI — create it from the
terminal instead: `New-Item -Path "filename" -ItemType File`.