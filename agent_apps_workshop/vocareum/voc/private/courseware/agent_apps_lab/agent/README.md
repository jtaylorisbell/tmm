# Your GM Service Assistant starter (all-OBO)

A minimal, working **OpenAI Agents SDK** dealer service-support agent for Databricks Apps. It's
wired to the shared GM assets and designed so **you can deploy it with no admin grants**.

**How it works (the important pattern):**
- **Every tool runs on-behalf-of-YOU (OBO):** each request reads your forwarded token
  (`X-Forwarded-Access-Token`) and calls Unity Catalog as you — so the `repair_orders` PII **column
  mask follows your identity** (Module 3's governance story) and you need no grants on the shared data.
- Tools (all against `agent_apps_workshop.shared`, via the `agent-apps-shared` SQL warehouse):
  `get_vehicle_details`, `get_warranty_policy`, `get_service_status` (governed PII), `search_vehicles`
  (Vector Search on `vehicle_docs_vs`).
- **LLM** uses `databricks_openai.AsyncDatabricksOpenAI()` (the FMAPI-aware client — don't hand-build
  an `AsyncOpenAI(base_url=...)`); it runs as the app service principal (pay-per-token, no grant). Its
  serving endpoint is governed by **Unity AI Gateway** (inference-table payload logging, usage/rate
  limits; guardrails intentionally off — they gate/stream-break this app, a Module 6 topic).
- App-level `user_api_scopes` must be `[sql, vector-search]` (enables the OBO token).
- **Streaming, visibly:** replies print token-by-token (SSE via `POST /chat/stream`) and tool
  calls appear as repair-order line items *while the agent works*. If a stream fails before anything
  renders, the UI falls back to the classic `POST /chat` path automatically.
- **Session journal:** the ≡ JOURNAL button lists this app's past conversations straight from
  its own Lakebase tables (`agent_sessions` / `agent_messages` — the same ones you browse in
  Module 5½); click an entry to reload and resume it.
- **Conversation memory (Lakebase):** the app is created with a `postgres` resource and writes
  transcripts **as its own service principal** into a per-app schema (`memory_<app-name>`). If
  memory is unavailable the chat degrades gracefully to single-turn ("memory: off" in the header).

**Deploy it:** open **`02_Deploy_App`** (one folder up) and **Run All** — or ask Genie Code, which
runs that same notebook. It handles the full sequence (app + Lakebase resource → Postgres role →
OBO scopes → deploy) and is safe to re-run any time. (In the lab you can't use the CLI for
mutating ops — the notebook uses the Apps REST API.)

Then **chat in the app's browser UI** (`who am I?` → your name; `status of RO-10001?` → redacted PII).
Validate in the browser, not a notebook POST — the OBO token only exists in a real app session.
