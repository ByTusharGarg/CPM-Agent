# CPM Agent: AI construction project manager harness

An always-on **project manager agent** that supervises specialist agents (design, procurement, site updates, cost, planning, reporting) across multiple construction projects. It tracks scope, budget and timeline, alerts the owner when the project slips, proposes costed recovery options, and acts on the owner's decisions.

The harness design follows **DeepSeek's super-agent prototype** ([`deepseek-ai/dsh-superagent-prototype`](https://github.com/deepseek-ai/dsh-superagent-prototype)), re-implemented in Python for construction.

## Architecture

```
Next.js chat-first UI ──REST + SSE──► FastAPI
   Event sources ─► fast-path rules ─► Thread ─┬─ subscribed worker? ─► Worker (direct delivery)
   (owner chat, contractor updates,             └─ else ─► PM Router (batched per project)
    engine rule hits, scheduler, watchdog)                   ├─ notify_owner / open_alert / present_alert
                                                             └─ dispatch_work ─► Worker sessions (async)
   Tracking engine (deterministic SPI/CPI/EAC/forecast) ◄── every number agents quote
   Authorization: off / observe / auto / approval      ◄── every side effect
   Watchdog (stuck / over-budget / drifting workers) · Scheduler (daily check, weekly report)
```

| DeepSeek idea | Where it lives |
|---|---|
| Router talks to people and never executes work | `backend/app/harness/router.py`. Tools are read-only plus dispatch, alert and notify. |
| Isolated worker sessions, one per task | `harness/worker.py` and `harness/workers/*.py`. Transcripts are persisted after each turn, so they can be resumed. |
| Two-stage filter (code rules, then model policy) | `harness/events.py` (allowlist, dedupe), then the Router prompt |
| Threads + subscriptions + direct delivery | `harness/events.py` and `harness/runtime.py`. For example, a contractor's later reports go straight to the worker that handles that contractor. |
| Router batching | `Runtime._router_loop`. Events that queue up while the Router is busy are handled in one call. |
| Context-as-cache + reference graph | `harness/context.py`. `lookup("milestone:3")` returns the record plus related refs. |
| Watchdog | `harness/watchdog.py` |
| Observe → auto authorization | `harness/authz.py`. Baseline schedule and budget changes are locked to owner approval. |
| State/compute separation | All state is in SQLite. `Runtime.resume()` restarts interrupted work after a restart. |

**Security:** contractor text is fenced as untrusted data. The Site Updates worker can only record progress and send reminders, so a prompt-injected report cannot change the plan. Contractors must be allowlisted and must send their submit token.

## Run it

Requires Python 3.11+ and Node 20+.

```bash
# database (Postgres; SQLite also works for quick local runs)
psql -U postgres -c "CREATE ROLE cpm LOGIN PASSWORD 'cpm_dev'" -c "CREATE DATABASE cpm OWNER cpm"

# backend
cd backend
python -m venv .venv && .venv/Scripts/activate      # (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env                               # Postgres URL, dev login codes, CORS, SMTP...
python -m app.seed --reset                         # runs migrations; seeds an org, 2 projects and a user per role
set ANTHROPIC_API_KEY=sk-ant-...                   # or: export ANTHROPIC_API_KEY=...
uvicorn app.main:app --port 8010                   # applies Alembic migrations on startup

# frontend (in another terminal)
cd frontend
npm install
echo NEXT_PUBLIC_API_URL=http://localhost:8010 > .env.local
npx next dev -p 3010                               # open http://localhost:3010
```

**Local model (Ollama) for offline testing:**
```bash
winget install Ollama.Ollama          # or https://ollama.com/download
ollama pull qwen3:8b                  # ~5 GB, supports tool calling
set CPM_LLM_PROVIDER=ollama           # optional: CPM_OLLAMA_MODEL, CPM_OLLAMA_NUM_CTX (12288), CPM_OLLAMA_THINK (off|on|auto)
uvicorn app.main:app --port 8010
```
The harness is unchanged; `harness/ollama_llm.py` translates its Anthropic-style messages and tools to Ollama's `/api/chat`. Expect minutes per flow on a laptop GPU, and weaker judgement than Claude. It's useful for exercising the wiring and the loop guards, not for judging answer quality.

If you have no API key, set `CPM_STUB_LLM=1` to run the whole harness with an offline stub model. The wiring works the same way, but the answers are placeholders.

Ports 8000 and 3000 were already taken on the dev machine, so the instructions use 8010 and 3010. Allow your UI origin with `CPM_CORS_ORIGINS=http://localhost:3010` if you change it.

### Sign in and roles
Sign-in uses one-time codes sent by email. With `CPM_DEV_SHOW_OTP=1` (dev only) the code is shown on the login page instead, so no mail server is needed. The demo accounts are:

| Account | Role | Can |
|---|---|---|
| `owner@demo.local` | Organization admin = owner of every project | Everything, including approving baseline/budget changes, editing autonomy modes and managing the team |
| `pm@demo.local` | Owner's rep | Chat, decide alerts, approve non-baseline actions, run checks |
| `consultant@demo.local` | Consultant | Read everything, run checks, request reports |
| `viewer@demo.local` | Viewer | Read-only |
| `site@apexsteel.example` | Contractor (Apex Steel) | Only the daily report form for their own company, with photos |

The permission matrix lives in `backend/app/auth.py::PERMISSIONS` and is enforced on every API route. Projects belong to an organization, and every project record is reached through project membership.

**Notifications:** alerts and approval requests go to the people who can act on them. Each user sets channels, an "immediately when severity ≥" threshold, quiet hours (critical still gets through) and a daily digest under **🔔 Settings**. Email goes out over SMTP (`CPM_SMTP_*`). Without SMTP, notifications stay in the outbox you can see on that page. WhatsApp arrives in phase 8.

### Schedule and critical path (phase 9)
A project can be tracked by milestones only, or by an **activity network**: activities with FS/SS/FF/SF links, lags and leads, on a working-day calendar (`Project.work_days`, Mon–Sat by default, plus `holidays`). With a network, `tracking/cpm.py` runs a forward and backward pass:
- Early and late dates, total and free float against the target finish. Negative float means late.
- The critical (longest) path and what drives each activity's start: a predecessor link, a material delivery, a design approval, or the data date.
- Progress handling: work in progress finishes after its remaining duration from the data date, and finished work keeps its actual dates.
- Milestones become roll-ups of their activities, so the health engine, alerts and owner reports all use the CPM forecast.

- **Import:** under **Schedule → Import MS Project / P6** (owner only), upload an MS Project XML (`.xml`) or Primavera P6 (`.xer`) file. Top-level WBS nodes become milestones, leaf tasks become activities, and the file's dates become the new current **baseline**.
- **Gantt:** shows baseline vs forecast, the critical path in red, progress, the data date and the target line. **Lookahead** lists work in progress or starting in the next N weeks and what it's waiting on. Contractors see only their own activities.
- **Agents:**
  - The Planner uses `get_schedule` and `lookahead`. Every what-if in `simulate_schedule` runs on the CPM, so it knows that only critical-path work moves the finish date.
  - Site Updates records progress on activities.
  - `schedule.rebaseline` (the current forecast becomes the new baseline) always needs the owner's approval.

### WhatsApp and email (phase 8)
People use the project from their phone. Each person is identified by the WhatsApp number or email on their profile (**Settings**), and their role decides what a message means:

| Who | Sends | Happens |
|---|---|---|
| Contractor | text, photos (before or with a caption), voice notes | A daily report is filed and goes straight to the Site Updates worker, which sees the photos as images. They get a ✅ receipt. |
| Owner / owner's rep | a question | It goes to the project manager, and the answer comes back on WhatsApp (and in the web chat) |
| Owner / owner's rep | taps a button, or replies `2` / `approve` | The decision or approval is recorded and follow-up work starts. Baseline changes stay owner-only. |
| Viewer / consultant / unknown number | anything | A read-only notice, or silently dropped for unknown numbers |

Decision requests go out as WhatsApp reply buttons, and as emails with signed links. A link opens a confirm page, so mail scanners that pre-fetch links can't decide anything. A plain email reply also works. Outside WhatsApp's 24-hour window, one approved template nudge is sent, and the waiting messages are delivered as soon as the person replies. Someone on several projects starts a message with `#<project>`.

**Connecting real accounts:**
1. In Meta for Developers, create an app with the WhatsApp product. Note the phone number ID, generate a permanent token, and copy the app secret.
2. Expose the API publicly (for local testing, `ngrok http 8010`). Set the webhook URL to `https://<public>/api/channels/whatsapp/webhook`, set the verify token to your `CPM_WA_VERIFY_TOKEN`, and subscribe to **messages**.
3. Create and get approval for a template named `cpm_alert` with one body variable, for example: *"Site Manager: {{1}}. Reply to this message to see the details and options."*
4. Email: point SendGrid Inbound Parse (or a Mailgun route) at `https://<public>/api/channels/email/inbound/<CPM_EMAIL_INBOUND_SECRET>`, and set the `CPM_SMTP_*` variables for sending.
5. Fill in `backend/.env` (see `.env.example`), restart, and check **Settings → Channels on this server**.

Inbound text is untrusted: it only ever becomes event data, never instructions. Messages are deduplicated by provider id, and every inbound message is logged with what was done with it (`inboundmessage` table).

**Scaling out:** with `CPM_MULTI_INSTANCE=1` and Postgres, several API instances can share one database. Postgres advisory-lock leases make sure exactly one instance runs each project's Router and each worker, and when an instance dies, another takes over.

**Migrations:** after changing `models.py`, run `alembic revision --autogenerate -m "..."` and commit the new file in `migrations/versions/`.

### Try it
1. Ask *"How are we doing?"* The manager reads the engine's numbers and answers in a streamed reply.
2. Click **Run check** in the right panel. The engine's rule hits are batched into one Router call. The Router opens alerts, dispatches the Planner to find recovery options, and presents a **decision card**.
3. Pick an option. The decision is recorded and follow-up work is dispatched. Actions in *observe* mode show up under **Autonomy → Observe log** instead of executing.
4. Open **Daily report form** (or sign in as `site@apexsteel.example`), submit a report with a photo, and watch the Site Updates worker record progress. A second report from the same contractor is delivered directly to that worker.
5. **Reports → Generate now** has the reporter worker write the weekly owner report.

### Terminal smoke test
```bash
python -m app.cli health --project 1
python -m app.cli chat --project 1 "What's blocking the steel?"
python -m app.cli check --project 1
```

### Tests
```bash
cd backend && python -m pytest -q
# whole suite on Postgres:
CPM_TEST_DB_URL=postgresql+psycopg://cpm:cpm_dev@localhost/cpm_test python -m pytest -q
```
The tests use a scripted offline LLM. They cover engine maths, alert rules, Router batching, dispatch and direct delivery, the observe/approval modes, the Watchdog, resume after a restart, and the alert → decision → follow-up flow.

## Configuration (env vars)
| Var | Default | |
|---|---|---|
| `CPM_ROUTER_MODEL` / `CPM_WORKER_MODEL` / `CPM_WATCHDOG_MODEL` | `claude-opus-5-5` | Model for each role |
| `CPM_ROUTER_EFFORT` / `CPM_WORKER_EFFORT` | `medium` / `high` | Thinking effort |
| `CPM_STUB_LLM` | `0` | Offline stub model |
| `CPM_DAILY_CHECK_HOUR`, `CPM_WEEKLY_REPORT_DAY` | `7`, `mon` | Scheduler |
| `CPM_WATCHDOG_STUCK_MINUTES` | `10` | Watchdog |
| `CPM_AS_OF_DATE` | today | Pin "today" for demos/tests |
| `CPM_DATABASE_URL` | `sqlite:///./cpm.db` | Swap for Postgres later |

Claude calls stream, use adaptive thinking, and set `fallbacks: "default"` (server-side refusal fallback, beta `server-side-fallback-2026-07-01`). The stable system prompt and tools are prompt-cached.

## Extending
- **New specialist:** add `harness/workers/<name>.py` with a `SPEC`, and register it in `workers/__init__.py`.
- **New event source** (WhatsApp, email, a vendor portal): call `runtime.emit(project_id, source, kind, payload, thread_key=...)`.
- **New side effect:** register it with `@action(...)` in `harness/authz.py` and give it a default mode in `DEFAULT_POLICIES`.
