# IBM Quantum · Live Telemetry Dashboard

A real-time operations dashboard for the IBM Quantum QPU fleet — device status,
queue depth, live job flow, and a scored recommendation of **which quantum
computer to submit to right now**.

It runs out of the box with **zero credentials**, using a physically plausible
simulator. Supply an IBM Cloud API key and it streams live data from the real
IBM Quantum Compute Service REST API.

```bash
# Terminal 1 — backend
python3 -m venv .venv && ./.venv/bin/pip install -r backend/requirements.txt
./.venv/bin/python -m uvicorn backend.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && npm install && npm run dev
```

Open **http://localhost:5173**. No configuration, no API key, no database setup.

---

## The problem

IBM rents time on real superconducting quantum processors. There are far more
researchers than machines, so you don't run your program — you join a queue.

That creates an ordinary operational problem: there are ~20 devices, some offline
for calibration, some with 3 jobs waiting and some with 400. Deciding where to
submit means clicking through IBM's portal one device at a time. There is no
single screen showing the whole fleet at once.

This is that screen.

Three real constraints shape the design:

| Constraint | Consequence |
|---|---|
| IBM tokens expire every hour | Something must re-authenticate silently and proactively, forever |
| The Runtime API is rate-limited per user | Request rate must be decoupled from viewer count |
| Networks and upstreams fail | A dashboard that shows a stack trace during a demo has failed |

---

## How it works

![System architecture](docs/architecture.svg)

**One background poller** hits IBM every 12 seconds and writes into an in-process
cache. Every browser, tab and REST caller reads that cache. IBM therefore sees a
**constant** request rate no matter how many people are watching — the single
most important property of the design.

**It cannot show an error.** Every snapshot carries a `source` field. If IBM
times out, rejects the token, or goes down for maintenance, the poller catches
it, substitutes simulated telemetry, stamps `source: "mock"` with a
human-readable reason, and the UI raises an amber banner. There is no error
screen and no blank state, because there is no error state to render.

Full design rationale, including what was rejected and why:
**[docs/architecture.md](docs/architecture.md)**.

---

## Features

- **Live QPU grid** — 18 devices, operational first then shortest queue, pulsing
  status dots, queue depth colour-coded by severity.
- **Fleet KPI bar** — devices online, qubits available, fleet-wide queue, median
  depth, jobs in flight.
- **Queue-depth chart** — the five busiest QPUs over time, seeded from stored
  history so it is populated on first paint rather than empty for a minute.
- **Smart recommender** — scores every operational device on queue depth (55%),
  throughput (25%) and qubit count (20%), normalised across the current fleet,
  and explains each ranking in a sentence.
- **Live job stream** — colour-coded status pills, relative timestamps, QPU
  charge time.
- **Honest mode indicator** — reflects the backend's actual `source`; it is a
  readout, not a switch the frontend could use to lie.
- **77 backend tests** covering token refresh, every IBM failure mode, the
  fallback guarantee, and recommender ordering.

---

## Enabling Live Mode

Mock mode is the default and needs nothing. For live IBM data:

### 1. Get an IBM Cloud API key

1. Sign in at **https://cloud.ibm.com**
2. Go to **Manage → Access (IAM) → API keys** (direct link:
   https://cloud.ibm.com/iam/apikeys)
3. **Create** → name it → **copy the key immediately** (it is shown once)

### 2. Find your Service CRN

1. Sign in at **https://quantum.cloud.ibm.com**
2. Open **Instances** and select your instance (create a free Open Plan instance
   if you have none)
3. Copy the **CRN**. It looks like:
   `crn:v1:bluemix:public:quantum-computing:us-east:a/abc123…::`

### 3. Configure

```bash
cp .env.example .env
```

Fill in the two values and restart the backend:

```env
IBM_QUANTUM_API_KEY=your-api-key-here
IBM_QUANTUM_CRN=crn:v1:bluemix:public:quantum-computing:us-east:a/...::
```

The mode indicator flips to **Live IBM QPU** on the next poll. If the credentials
are wrong, the dashboard keeps working on simulated data and tells you exactly
what IBM said.

> **EU region:** set `IBM_QUANTUM_API_URL=https://eu-de.quantum.cloud.ibm.com/api/v1`.

### Verifying credentials by hand

```bash
curl -s -X POST 'https://iam.cloud.ibm.com/identity/token' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d 'grant_type=urn:ibm:params:oauth:grant-type:apikey&apikey=YOUR_API_KEY'
```

Take the `access_token` from the response and call:

```bash
curl -s 'https://quantum.cloud.ibm.com/api/v1/backends' \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer YOUR_TOKEN' \
  -H 'Service-CRN: YOUR_CRN' \
  -H 'IBM-API-Version: 2024-01-01'
```

All three headers are required. Omitting `IBM-API-Version` is the most common
cause of an unexpected rejection.

---

## Configuration

Every setting has a working default; `.env` is only needed to change one.

| Variable | Default | Purpose |
|---|---|---|
| `IBM_QUANTUM_API_KEY` | *(blank)* | Blank ⇒ simulated telemetry |
| `IBM_QUANTUM_CRN` | *(blank)* | Instance CRN; both halves required |
| `IBM_QUANTUM_API_URL` | `https://quantum.cloud.ibm.com/api/v1` | Change for the EU region |
| `IBM_API_VERSION` | `2024-01-01` | Required request header; pin it |
| `POLL_INTERVAL_SECONDS` | `12` | **Values below 10 are rejected at startup** |
| `FORCE_MOCK_MODE` | `false` | Force simulation even with valid credentials |
| `ENABLE_HISTORY` | `true` | SQLite queue-depth history |
| `HISTORY_RETENTION_DAYS` | `7` | Rows older than this are pruned hourly |

---

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/telemetry` | Current fleet snapshot (initial paint) |
| `GET /api/health` | Poller state, mode, connected clients |
| `GET /api/recommendations?min_qubits=100` | Ranked submission targets |
| `GET /api/history?minutes=60&backend=ibm_fez` | Stored queue depth |
| `GET /api/history/busiest?hours=24` | Average and peak depth per device |
| `WS /ws/telemetry` | Live snapshot stream |
| `GET /docs` | Interactive OpenAPI documentation |

---

## Tests

```bash
./.venv/bin/python -m pytest -q
```

77 tests, ~0.6 s. They cover the claims this project makes rather than merely
exercising the code:

- Simulated queue depths **drift** rather than jump, so the chart is believable
- The poller returns a renderable fleet under **every** IBM failure mode
- A rejected API key latches, and its reason keeps being reported
- All three required IBM headers are sent
- IBM's nested `status` object and both job-status vocabularies parse correctly
- The recommender never ranks a swamped device above an idle one

---

## Docker

```bash
docker compose up --build   # → http://localhost:8080
```

Two services, `backend` and `frontend`. No Redis and no Postgres — a
single-operator dashboard with one poller has nothing to share between processes.
SQLite history persists on a named volume.

Verified end-to-end on Docker 29.7.2 / Compose v5.5.1:

| Check | Result |
|---|---|
| Both images build | clean |
| Startup ordering | frontend waits on the backend's `HEALTHCHECK` before starting |
| REST through nginx | all 5 endpoints `200` |
| WebSocket upgrade through nginx | connects, `ping`/`pong`, pushes at exactly 12.0 s |
| **5 concurrent clients** | all 5 received **1 distinct snapshot** — one poll served every client |
| Client reaping | count returns to 0 on disconnect |
| SPA fallback | unknown routes serve `index.html` |
| Cache headers | `index.html` `no-cache`; hashed assets `immutable`, 1 year |
| gzip | active on JS/CSS |
| Container user | `appuser`, non-root |
| Volume persistence | history survived `docker compose restart`; simulator resumed from stored depths |
| Credential passthrough | a bad key in compose env reached IBM IAM, was rejected, and the dashboard still served `200` with 18 backends |
| Browser console | **no errors, no warnings** |

Credentials are passed at runtime and never baked into an image; `.dockerignore`
excludes `.env`, `node_modules`, `.venv` and `.git` from both build contexts.

---

## Project layout

```
backend/
  main.py                 app wiring + lifespan
  config.py               settings, rate-limit floor enforcement
  models.py               Pydantic domain types shared by live and mock paths
  mock_service.py         stateful fleet simulator
  core/
    ibm_client.py         IAM token cache, typed IBM REST calls
    poller.py             the single heartbeat + fallback decision
    cache.py              TTLCache snapshot store
    history.py            SQLite queue-depth log
    analytics.py          fleet summary + recommender
  api/
    rest.py               cache-reading REST endpoints
    websocket.py          fan-out to connected clients
  tests/                  77 tests
frontend/src/
  App.jsx                 layout, degraded banners
  hooks/useQuantumTelemetry.js   WebSocket + backoff reconnect + chart buffer
  components/             QPUCard, QueueChart, JobStream, ModeToggle,
                          FleetSummary, Recommender
docs/architecture.md      design rationale
docker-compose.yml        backend + frontend, no broker
.dockerignore             keeps secrets and node_modules out of build contexts
```

---

## Notes and limitations

- **Estimated wait is derived, not reported.** IBM's documented `/v1/backends`
  response has no wait-time field. The figure comes from queue depth, a nominal
  service time and processor speed, and is labelled as an estimate throughout.
- **`/v1/jobs` returns your account's jobs**, not a global feed — IBM exposes no
  public job stream. In simulated mode the job stream is synthetic.
- **Run one backend worker.** The design assumes one poller per process;
  multiple workers would multiply the request rate to IBM.
