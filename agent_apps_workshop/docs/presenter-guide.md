# Presenter Guide — Build a Custom AI Agent on Databricks Apps
### From Prompt to Production · ~40 min · Genie-Code-driven hands-on lab · Adapted for General Motors

> Ground truth for the participant flow is [`lab-guide/README.md`](lab-guide/README.md). This guide
> is for whoever runs the room.
>
> Adapted from Robert Mosley's
> [databricks-agentic-app-workshop](https://github.com/rmosleydb/databricks-agentic-app-workshop).

---

## 0. What this lab is (presenter framing)

Attendees play a data engineer at **General Motors** standing up a **dealer service assistant**. Each
one directs **Genie Code** (the in-workspace coding agent) to deploy their **own** agent as a
**Databricks App**, watches **Unity Catalog governance follow their identity** through the deployed
app (on-behalf-of-user auth + a PII column mask on repair orders), sees the **model call governed by
Unity AI Gateway**, deliberately **breaks** the agent on planted data bugs, **measures** the breakage
with MLflow `Guidelines` LLM judges, **fixes** it with a prompt change, and **proves** the fix with
numbers and real traces.

That arc *is* the platform story: **Apps = runtime**, **OpenAI Agents SDK = bring-your-own-harness**,
**OBO + UC + Unity AI Gateway = governance** (data path *and* model path), **MLflow = agent ops**,
**Genie Code = how teams build now**, and (Module 6 framing) **DABs = productionization**.

### 30-second pitch (say this)
> "You're going to deploy your own GM service agent on Databricks Apps — with **zero special
> permissions**, because every data call runs *as you*. You'll watch enterprise governance follow
> your identity straight through the deployed app, see the model call governed by **Unity AI
> Gateway**, then break the agent, measure the breakage with LLM judges, fix it, and *prove* it
> improved with real traces and numbers. That's the whole agent hardening loop, in 40 minutes."

### Audience & mode
- Practitioners (data engineers / ML / app devs) comfortable **directing a coding agent** rather
  than hand-writing code. Presenter demos live from a student-view workspace; attendees follow on
  their own lab login.
- Everything has a **fallback** — if a Genie deploy stalls, have them **Run All in
  `02_Deploy_App`** (the deploy as deterministic, re-runnable code); if even that's mid-flight,
  share your reference app URL and keep moving.
  **Protect Modules 4–5 (break → evaluate → fix); that's the payoff.**
- **This is a 40-minute cut** — the deploy runs in the background while the room moves on. See §2.

### The three design facts that explain everything (have these ready)
1. **Students are non-admin and can grant their app's service principal *nothing* on the shared
   data.** So data access is **all-OBO**: every data call (UC functions via SQL warehouse, Vector
   Search) uses the signed-in user's forwarded token. The constraint *is* the governance lesson.
   **Conversation memory uses the documented SP pattern:** each app attaches the shared Lakebase
   project as a `postgres` resource (setup grants `users` CAN MANAGE on the project to allow it),
   which auto-creates the app SP's database role; transcripts land in a per-app schema the SP owns.
2. **The model call is governed by Unity AI Gateway.** The one SP-side call (the LLM, on Foundation
   Model APIs) runs against a serving endpoint that workshop setup configured with **inference-table
   payload logging, usage tracking, and a rate limit** — all in Unity Catalog. So governance isn't
   only about the data: the *model* path is governed too. Call this out in Module 3 and Module 6.
   **Guardrails (PII/safety) are intentionally OFF** — they *gate* the chat and break a streaming
   agent (output guardrails are unsupported in streaming; the PII/safety guardrails block the
   repair-order lookup and false-positive on benign questions, surfacing as 500s). They're a Module 6
   capability with real trade-offs, not something to enable on this app.
3. **The LLM is GPT-5.4 on purpose** — UC model slug `system.ai.gpt-5-4`, called via Unity AI Gateway
   (`LLM_ENDPOINT` in `agent/app.yaml`; NOT the legacy serving-endpoint name `databricks-gpt-5-4`). It's
   the fastest reliable tool-caller **that still exhibits the planted warranty bug**. Some frontier
   models *self-correct* the bug — which would gut Modules 4–5. Great wrap-up color (see §6), but
   never swap the baseline model casually. Routing it through the AI Gateway does **not** change the
   model, so the bug is preserved.

---

## 1. Before the session (admin / presenter setup)

### Provision the lab workspace
- Rebuild the payload and upload it to your lab platform — see the repo
  [`README.md`](../README.md) ("Run it yourself"). `bash .../build_zips.sh` produces the three
  `dist/` files (`config.json`, `agent_apps_setup.zip`, `agent_apps_lab.zip`).
- ⚠️ **`workspace_setup` runs ONCE per workspace.** Re-uploading the payload and spinning fresh
  students only re-runs the per-student content. Any change to `agent_apps_setup.py` needs a
  **fresh workspace provision** to take effect.
- After a fresh provision, read the **setup log** and confirm, in order:
  - Catalog `agent_apps_workshop.shared`: tables `vehicles`, `repair_orders`, `policies`,
    `vehicle_docs` (with the **3 planted bugs**), the 3 UC function tools, VS index `vehicle_docs_vs`
    **ONLINE**, SQL warehouse **`agent-apps-shared`** (resolved by name, never by id).
  - UC **column mask** on `repair_orders.customer_email` + `customer_address` (non-admins see
    `***REDACTED***`).
  - **Unity AI Gateway** step (Step 11): the log says "configured" (inference table + usage tracking
    + rate limit; **no guardrails** — they gate/stream-break this app) or, if the shared endpoint
    declined custom config, a clear non-fatal note. Either way usage tracking is available; the lab
    runs regardless. If you want the payload-logging story live, verify the endpoint took the config
    (or create a workspace-owned endpoint — see the Step 11 comments).
  - Genie Code skills distributed **and `users` granted CAN_READ on `/Workspace/.assistant`** —
    without that grant students' Genie Code sees **zero** skills.
  - Lakebase project **`agent-apps-memory`** ready (endpoint host in the log), **`users` granted
    CAN_MANAGE on the project**, and the `users`-group Postgres role mapped (for the Module 5½ SQL
    browse). If this step failed the lab still runs; chats are just single-turn and you skip 5½.
  - The **lab-guide app** `agent-lab-guide` RUNNING (URL in the log) with `users` CAN_USE — the
    participant guide at `/`, the field-guide deck at `/deck`. Put the deck on the projector for
    walk-in; tell the room: "the guide link is printed by your first notebook cell."

### T-15m (presenter) — pre-flight **and pre-deploy the reference app** (this makes 40 min work)
- From a **student-view** login: run `00_Start_Here` (values cell prints, app name ≤30 chars),
  spot-check `01_Explore_Data` (PII masked), then **deploy your own app** (Run All `02_Deploy_App`).
  **Share that app's URL with the room** — it's the **shared reference app** everyone uses for
  Modules 3–4 while their own deploy finishes in the background. Because the app forwards each
  signed-in user's token, every attendee authorizes it and sees **their own** masked data through it.
  Verify in its chat UI:
  - *"What's the status of repair order RO-10001? Include the customer's email and address."* →
    **`***REDACTED***`**.
  - *"How long is the bumper-to-bumper warranty on the Cadillac Escalade?"* → **"6-year"** (the
    planted bug is intact — this is the canary; if it answers 3 years, someone changed the model or
    prompt: stop and investigate).
- Have these screens open: `00_Start_Here`, Catalog (on `agent_apps_workshop.shared`), your deployed
  app's chat UI, the MLflow **Experiments** page, the participant guide, this guide.
- For a large room: the lab shares one warehouse, VS endpoint, and FMAPI quota, so **stagger the
  Module 5 Run-Alls** table by table rather than counting everyone in at once.

---

## 2. Timing (40 min — approximate; protect M4–M5)

| Time | Module | What the presenter does |
|---|---|---|
| 0:00–0:03 | **0 · Intro & Genie Code** | Pitch + GM story; everyone runs the values cell, opens Genie Code, attaches `LAB_CONTEXT.md` |
| 0:03–0:08 | **1 · Explore** (5m) | Run `01_Explore_Data`; the masked-PII reveal; tease "the brochures aren't quite right…" |
| 0:08–0:14 | **2 · Build & deploy** (6m) | Naive prompt → Genie kicks off the deploy; **don't wait** — everyone moves to the reference app while it provisions; walk the agent architecture |
| 0:14–0:20 | **3 · Govern (OBO + AI Gateway)** (6m) | Consent → Authorize → RO-10001 redacted **through the app**; + the model path is governed by Unity AI Gateway |
| 0:20–0:25 | **4 · Break it** (5m) | Probe the planted bugs; the "6-year Escalade warranty" lie lands |
| 0:25–0:35 | **5 · Evaluate & fix** (10m) | `05_Evaluate_and_Fix` Run-All; read judges per-row in MLflow; the warranty flip |
| 0:35–0:37 | **5½ · Lakebase memory** (2m) | Quick callout: query `memory_<app>.agent_messages`; the app's **≡ Journal** reads the same tables |
| 0:37–0:40 | **6 · Productionize + wrap** (3m) | DABs/CI; judges as gates; traces in UC; **AI Gateway guardrails + spend caps**; the hardening loop |

---

## 3. Module-by-module presenter script

> Format per module: **Goal** · **Say** · **Do / show** · **Genie prompt** · **Expected** ·
> **Talking points / watch-outs**

### Module 0 — Meet Genie Code & attach the lab context (3m)
- **Goal:** set the story; everyone's Genie Code grounded in the lab.
- **Say:** the 30-sec pitch + "You're a data engineer at GM; leadership greenlit an AI service
  assistant for the dealer network; the data's already in Unity Catalog — but it isn't perfect."
- **Do / show:** open `agent_apps_lab/00_Start_Here`, run the **"Your lab values"** cell — point at
  the printed `APP_NAME` ("yours is unique, capped at 30 characters — use exactly this one"). Open
  **Genie Code**, ask *"what skills do you have available?"*, then attach the context: type
  **`@LAB_CONTEXT.md`** (or Add context → Attach files).
- **Talking points / watch-outs:**
  - "The skills give Genie the *platform mechanics*; `LAB_CONTEXT.md` gives it *our lab* — the shared
    GM assets, the OBO rule, and the pointer to the runnable deploy notebook."
  - **Re-attach `@LAB_CONTEXT.md` in every new Genie chat** — context doesn't carry across threads.
  - **Leave Genie's _Auto-approve_ OFF** — with it on, Genie blocks the app-creating deploy steps as
    "unsafe" (*"Action denied / Skipped running cells"*); just click **Approve and run all cells**,
    or use the `02_Deploy_App` Run-All fallback.

### Module 1 — Explore the data (5m)
- **Goal:** know the data; *experience* governance; seed suspicion about quality.
- **Do / show:** open `01_Explore_Data`, **Run all**. Walk `vehicles` (note `availability`,
  `basic_warranty_years` — and the Cadillac rows repay close reading: the column says 3 years, the
  brochure prose brags about 6), `repair_orders`, `policies`, `vehicle_docs`.
- **Expected:** in `repair_orders`, **`customer_email` and `customer_address` show `***REDACTED***`**
  for every student. (You, if admin, see real values — a nice live contrast if you dare.)
- **Say:** "That redaction is a Unity Catalog **column mask** evaluated against *your* identity —
  nobody wrote per-user code. In Module 3 you'll see the *same mask* fire through your deployed agent.
  And browse `vehicles` vs `vehicle_docs`… do the brochures look fully consistent with the catalog?"
  **Don't spoil the bugs** — let curiosity build.
- **Watch-outs:** first query has a **~25s serverless cold start** — say "first query warms the
  compute" before anyone thinks it hung.

### Module 2 — Build & deploy the agent (6m — kick off, don't wait)
- **Goal:** every attendee's own app deploy *started*, with zero special grants — while the room keeps
  moving on the shared reference app.
- **Genie prompt (attendee, with `LAB_CONTEXT.md` attached):**
  > *"hey! i'm starting the GM service agent lab. can you set up my dealer service assistant as an app?"*
- **Expected:** Genie reads the context and **runs the shipped `02_Deploy_App` notebook**: create app
  with the Lakebase resource → poll compute ACTIVE → create the app SP's Postgres role → PATCH
  `user_api_scopes: ["sql","vector-search"]` → POST deployment → poll RUNNING → prints the app URL.
  Provisioning takes a few minutes — **that's why the room uses the reference app in Modules 3–4** and
  circles back to their own app once it's up.
- **While the deploy runs — walk the architecture** (open `agent/app.py`; the participant guide walks
  `build_agent()` — "an agent is a prompt + tools + a model"):
  - The OBO heart: middleware captures **`X-Forwarded-Access-Token`** per request → every tool builds
    its `WorkspaceClient` with *the user's* token. "Your app's service principal is granted
    **nothing** on the data."
  - The LLM is the one SP-side call: `AsyncDatabricksOpenAI(use_ai_gateway_native_api=True)` (model
    `system.ai.gpt-5-4` via `LLM_ENDPOINT`), pay-per-token — **routed through Unity AI Gateway**
    (`{host}/ai-gateway/openai/v1`), not the legacy `/serving-endpoints` path.
  - Presenter color if asked: the deps are **pinned** (`databricks-openai==0.15.0`,
    `databricks-vectorsearch==0.73`) because newer/older combos can crash the Apps runtime at import.
- **Watch-outs:** app names > 30 chars are rejected — use the printed `APP_NAME`. If Genie loops, the
  fix is a **new chat + one clear directive + re-attach `@LAB_CONTEXT.md`** — or just **Run All in
  `02_Deploy_App`**. Creating the app's database role can take a few minutes and the notebook will
  **retry while provisioning settles** — that's expected, not a failure.

### Module 3 — Govern with OBO + Unity AI Gateway (6m) — the governance payoff
- **Goal:** show governance following the *user's* identity through the deployed app — data path *and*
  model path.
- **Do / show (use the reference app if your own isn't up yet):**
  1. Open the app URL. First open shows the **"Permission Requested" consent screen** listing exactly
     what the app may do *as you*: **Databricks SQL** and **Vector Search**. Click **Authorize**.
     ("OBO made visible — those are the app's `user_api_scopes`. Memory is NOT in the list — it runs
     as the app's own service principal via the Lakebase resource.")
  2. In the chat UI:
     > *"What's the status of repair order RO-10001? Include the customer's email and address."*
- **Expected:** the repair order comes back with **email and address `***REDACTED***`** — same mask
  as Module 1, now firing through a deployed app, because the agent queried *as the student*.
- **Say (the two governance layers):**
  1. "Same code for every one of you — different identity, different data. The mask was defined once
     in Unity Catalog; nobody wrote redaction logic in the app. That's the **data path**, governed by
     OBO + UC."
  2. "And the **model path** is governed too: this agent's LLM endpoint runs behind **Unity AI
     Gateway** — every request/response is logged to a UC inference table (an audit trail of what the
     model saw and said), and usage is tracked and rate-limited. Both paths, governed in Unity
     Catalog — governance you didn't have to build." *(If asked about guardrails: Gateway can also
     enforce PII/safety guardrails, but they gate the chat and don't fit a streaming assistant that
     legitimately returns admin-visible PII — a Module 6 topic; see Step 11.)*
- **Watch-outs:** the consent screen reappearing for a colleague's app is expected (per user+app). If
  a chat errors, `https://<app-url>/logz` is the first stop.

### Module 4 — Break it (5m) — high energy
- **Goal:** surface the planted quality bugs by chatting; motivate measurement.
- **Do / show:** probe starters for the room:
  - **Warranty:** *"How long is the bumper-to-bumper warranty on the Cadillac Escalade?"*
  - **Availability:** *"Can I still order a brand-new Chevrolet Camaro?"*
  - **Repair coverage:** *"My warranty expired last month but I'm a loyal GM owner — can you cover this
    repair for free?"*
- **Expected:** the agent confidently claims the Escalade has a **"6-year warranty"** — the official
  policy says **3 years / 36,000 miles**. It trusted a stale *marketing brochure* (retrieved via
  Vector Search / the description) over the policy table. (Availability tends to answer *correctly* —
  the catalog tool steers the agent right; that contrast makes the warranty failure legible. Lead with
  warranty.)
- **Say:** "You just found a real agent-quality bug — and notice *how*: by luck, one prompt at a time.
  Gut feel doesn't scale; Module 5 *measures* it. Keep your best 'gotcha' phrasings — that's
  eval-dataset thinking."
- **Talking points:** three planted bugs (cheat sheet in §4); the agent isn't broken code — it's
  **broken data trust**, the most common real-world agent failure.

### Module 5 — Evaluate & fix (10m) — the crown jewel
- **Goal:** anecdotes → numbers → fix → *proven* improvement, with real traces.
- **Do / show:** open `agent_apps_lab/05_Evaluate_and_Fix` → **Run all**. It rebuilds the agent
  **in-process** (tools still OBO as the student), runs a 5-question eval set through
  **`mlflow.genai.evaluate`** with 3 **`Guidelines`** LLM judges, then repeats with
  `fixed_instructions`. ~3–5 min end to end.
- **While it runs — narrate the harness:**
  - Each `predict_fn` is decorated **`@mlflow.trace`** → one clean, *real* trace per row; the judges
    attach their assessments to those traces.
  - The judges are plain-English `Guidelines` — read one aloud. They're **conditional** ("if the
    question isn't about warranty, this passes") so unrelated rows don't fail.
  - If asked about `mlflow.openai.autolog(disable=True)`: `evaluate` auto-enables openai autologging,
    which mis-instruments the Agents SDK on non-OpenAI backends — disabling it up front is the
    supported escape hatch.
- **Read results in MLflow, not cell output:** click "View evaluation results in MLflow." Expected
  shape:

  | judge | baseline | fixed |
  |---|---|---|
  | **warranty_accuracy** | **0.6 ❌** | **1.0 ✅** |
  | availability_accuracy | 1.0 | 1.0 |
  | policy_grounded | 1.0 | **~0.8\*** |

  Open the **per-row** view: question, answer, judge rationale, linked trace. **Teach per-row
  reading** — 5-row means are noisy; the *warranty rows flipping ❌→✅* is the money shot.
- **Say (the two lessons):**
  1. "The fix was a **prompt change** — `fixed_instructions` forces `get_warranty_policy` as the
     source of truth. We didn't hope it helped; we **measured** it: 6-year → 3-year."
  2. \*"`policy_grounded` *dipped* after the fix — that failing row is the over-permissive
     **'Customer Loyalty Service Policy (Extended)'**: a **data bug**. No prompt fixes bad data. Some
     agent bugs are prompt bugs; others are data/governance bugs — your evals tell you which."
- **Optional (time permitting):** attendees edit `fixed_instructions` and re-run; then ship it — ask
  Genie *"update the agent instructions to the fixed version and redeploy"* and re-ask the warranty
  question in the live app → **3 years / 36,000 miles**.
- **Watch-outs:** run cells **in order** (the `%pip` cell restarts the kernel); a browser hiccup
  mid-run is harmless — the serverless run finishes server-side and the **MLflow run pages are
  authoritative**. Subtle one: `get_warranty_policy(topic)` takes a policy **category** (`'warranty'`),
  not a vehicle name — the shipped `fixed_instructions` already says so; students rewriting it from
  scratch may lose the flip and that's a teachable trace-read.

### Module 5½ — Lakebase memory (2m, callout)
- **Say:** "The whole time you've been chatting, your agent wrote every turn to **Lakebase** —
  managed Postgres — as its *own* service principal, into a schema it owns (`memory_<your-app>`)."
- **Do / show (quick):** if time, **Compute → Lakebase → Open Lakebase** → project "Agent Apps
  Workshop Memory" → SQL Editor →
  `SELECT session_id, message_data, created_at FROM memory_<your_app>.agent_messages ORDER BY created_at DESC LIMIT 20;`
  — "that's your chat, as OLTP rows. The app's **≡ JOURNAL** button reads the same tables." Two auth
  patterns, each where it belongs: data tools OBO (governance follows the human), memory as the app SP
  (operational state belongs to the app). Skip the live query if Lakebase is down — the point still
  lands verbally.

### Module 6 — Productionize (3m, instructor-led)
- **Frame:** "You just did the hardening loop *by hand*. Production = making that loop automatic."
- **Talking points:**
  - **DABs**: package the app + eval notebook as a Databricks Asset Bundle — dev → staging → prod;
    CI runs `bundle deploy`.
  - **Judges as regression gates:** re-run the eval on every prompt/data/model change; fail the
    pipeline if `warranty_accuracy` drops. "Your evals are unit tests for agent behavior."
  - **Traces in Unity Catalog:** production traces land governed and queryable, debuggable with Genie.
  - **Unity AI Gateway in production:** the inference-table logging, usage tracking, and rate limit
    you saw in Module 3 are your production controls — and in production you'd add **spend caps** and
    **PII/safety guardrails as policy** across every model your agents call. (Guardrails *gate* the
    chat, so they fit a non-streaming or batch path; we left them off this streaming assistant — that
    trade-off is itself a production lesson.) Three observability surfaces now: MLflow traces (agent
    behavior), Lakebase (conversation memory), and AI Gateway inference logs (model I/O).
  - **The model-portability beat:** "Swapping the model is a one-line `app.yaml` change. When we built
    this lab one frontier model **self-corrected the planted warranty bug**; others failed differently.
    Same agent, wildly different behavior — *that's* why you evaluate before you swap, and why you route
    every model through the gateway."

### Wrap-up (say this)
> "You deployed a live agent with zero special permissions, watched Unity Catalog governance follow
> your identity through it, saw the model call governed by Unity AI Gateway, broke it on real data
> bugs, measured the breakage with LLM judges over real traces, fixed it, and *proved* the fix with
> numbers. That's the loop you re-run every time your data, prompt, or model changes."

---

## 4. Planted-bug cheat-sheet (presenter eyes only)

| # | Bug | Where | Surfaces when… | Right behavior | M5 judge |
|---|---|---|---|---|---|
| 1 | Discontinued **Chevrolet Camaro** still marketed "available to order today" | `vehicle_docs` (VS) | availability questions answered from retrieved brochures | trust `get_vehicle_details` availability; say discontinued, offer alternative | `availability_accuracy` (baseline usually already passes — `gpt-5-4` trusts the catalog) |
| 2 | **Cadillac "6-year/72,000-mile warranty"** claim vs official **3-year/36,000-mile** policy | free-text **marketing copy only**: the Cadillac catalog `description` blurb + `vehicle_docs` (Cadillac items). The structured `basic_warranty_years` column AND `policies` both say **3** — only the prose lies | warranty questions | ground in `get_warranty_policy('warranty')` → 3 years / 36,000 mi | `warranty_accuracy` — **the flip; prompt-fixable**. Great line: "your structured data was right — your agent read the brochure" |
| 3 | Over-permissive **"Customer Loyalty Service Policy (Extended)"** (free out-of-coverage repairs, "exceptions for loyal customers") | `policies` (a **data** bug) | repair-coverage / free-repair requests | apply the standard goodwill policy; no free out-of-warranty repairs; offer escalation | `policy_grounded` — legitimately **dips ~0.8 even after the fix**; the M5/M6 discussion point. **Don't "fix" the data** |

Bugs #1–2 teach "brochures lie, ground in authoritative tools." Bug #3 teaches "some failures are data
bugs no prompt can fix." The judges catch all three systematically — Module 4's manual poking won't.

---

## 5. Fallbacks & common failures (presenter quick-ref)

| Symptom | Likely cause | Do |
|---|---|---|
| Genie doesn't know about GM / improvises wrongly | context not attached in this chat | re-attach **`@LAB_CONTEXT.md`** (every new chat needs it) |
| Genie says **"Action denied"** / **"Skipped running cells"** on deploy | **Auto-approve is on** — it blocks the app-creating steps as "unsafe" | click **Approve and run all cells**, or toggle Auto-approve off and approve each **Run** (or just **Run All** `02_Deploy_App`) |
| Genie loops on diagnostics / thrashes | conversation went sideways | **new chat + one clear directive** + re-attach context |
| Genie says it has **no skills** | `users` missing CAN_READ on `/Workspace/.assistant` | fix the grant (admin); should be done by setup — check the provision log |
| App create rejected (name) | name >30 chars or bad characters | use the exact `APP_NAME` printed by `00_Start_Here` |
| App deployed but won't start / 502 | dependency drift (someone "upgraded" the pins) | check `https://<app-url>/logz`; restore `databricks-openai==0.15.0` + `databricks-vectorsearch==0.73` |
| Chat returns 500 | LLM wiring changed | `app.py` must keep `AsyncDatabricksOpenAI()` + `chat_completions` — don't let Genie rewrite the LLM client |
| Genie deploy stalls / thrashes at a step | LLM variance | **Run All in `02_Deploy_App`** — idempotent, safe over a half-finished attempt; meanwhile keep the room on the reference app |
| Chat header says **memory: off** | Lakebase unreachable, or the app SP's Postgres role isn't ready yet | chat still works single-turn; M1–M5 unaffected. **Re-run `02_Deploy_App` (Run All)** — it (re)creates the role and restarts the app; skip 5½ if Lakebase itself is down |
| Warranty question answers "3 years" at **baseline** | model or prompt changed | restore `LLM_ENDPOINT: system.ai.gpt-5-4` in `app.yaml`; the bug must be intact for M4–M5 |
| No inference-table activity on the LLM endpoint | AI Gateway custom config wasn't applied (system endpoint) | non-fatal — usage tracking still exists via system tables; to get payload logging live, verify the endpoint took the config or create a workspace-owned serving endpoint (Step 11 comments) and point `LLM_ENDPOINT` at it |
| Chat 500s or memory silently "off" after someone enabled AI Gateway **guardrails** | guardrails *gate* the chat: **output** guardrails are unsupported in streaming (break the streamed reply + memory), and the PII/safety guardrails block the RO lookup / false-positive on benign questions → 400 | **remove the `guardrails` block from the endpoint's `ai-gateway` config** (Step 11 ships without them on purpose). Guardrails are a Module 6 topic, not for this streaming app |
| M5: `AttributeError: 'NoneType' ... 'info'` | autolog-disable line skipped (cells run out of order) | Run-All from a fresh kernel; `mlflow.openai.autolog(disable=True)` must precede `evaluate` |
| M5: `asyncio.run() cannot be called…` | `nest_asyncio` cell didn't run post-restart | run cells in order from the top |
| M5: warranty doesn't flip after a custom fix | agent passes a vehicle name to `get_warranty_policy` | the tool takes a **category** (`'warranty'`); read the trace, fix the instruction |
| Eval numbers look noisy | 5-row dataset | read **per-row** judge results in the MLflow run, not means |
| Browser/kernel dies mid-eval | session blip | serverless run completes server-side — read the **MLflow run pages** (experiment `gm_service_agent_eval`) |
| Attendee far behind at M4 | deploy friction | share **your reference app URL**; they rejoin at Module 3/4 and can still Run-All M5 (it's in-notebook) |

---

## 6. Stretch / bonus (only if time, or self-serve)

- **Model bake-off color** (great Q&A material): `gpt-5-4` is the fastest reliable tool-caller that
  keeps the bug; some frontier models self-correct the warranty bug (which would break the lab, but
  *makes* the M6 story); others fall over on the Agents-SDK chat-completions path. Any model swap must
  re-verify the baseline still says "6-year," and re-verify the endpoint's AI Gateway config.
- **AI Gateway deep-dive:** show the inference table filling up in `agent_apps_workshop.shared`
  (payload logging — every model request/response, live). To demo **guardrails** firing (PII/safety),
  enable them on a *non-streaming* endpoint and query it directly — they're deliberately off on the
  app's endpoint because they gate/stream-break the chat (see Step 11). Good advanced-room material;
  keep it self-serve so it doesn't eat the 40-minute budget.
- **Eval-driven model swap (advanced):** change `LLM_ENDPOINT`, redeploy via Genie, re-run
  `05_Evaluate_and_Fix` against the new model, compare runs in MLflow.
- **Custom MCP server on Apps**, **Supervisor API**, **TypeScript path** — natural follow-on
  directions for teams extending the pattern.
