# Databricks notebook source
# MAGIC %pip install --quiet --upgrade databricks-sdk
# MAGIC %restart_python

# COMMAND ----------

# MAGIC %md
# MAGIC # agent_apps_setup — workspace provisioning for "Hands on: Agent Apps Workshop"
# MAGIC
# MAGIC **Framework-native `workspace_setup` notebook** (the bricks paradigm). The DB Academy / Vocareum
# MAGIC framework runs this once per shared lab workspace, as the **privileged setup identity**, on
# MAGIC **serverless job compute** — declared via `workspace_setup` + `serverless_job_cluster: true` in
# MAGIC `config.json`. The framework injects `host` and `token` as notebook parameters.
# MAGIC
# MAGIC By the time this runs, the framework's `workspace_init()` has already **assigned a unique Unity
# MAGIC Catalog metastore** (via `metastore_config: {unique: true}`), so UC is enabled and we can
# MAGIC `CREATE CATALOG` directly with `spark.sql`.
# MAGIC
# MAGIC **Shared model:** everything below is created ONCE, as the privileged user, and shared by all
# MAGIC students (no per-user schemas). Provisions (idempotent):
# MAGIC 1. Catalog `agent_apps_workshop` + `shared` schema
# MAGIC 2. Source tables `vehicles`, `repair_orders`, `policies` (data embedded below — self-contained)
# MAGIC 3. `vehicle_docs` (derived from vehicles, CDF enabled for Vector Search)
# MAGIC 4. 3 intentional quality **bugs** students discover and fix
# MAGIC 5. Vector Search endpoint + `vehicle_docs_vs` delta-sync index
# MAGIC 6. The 3 **UC function tools** (`get_vehicle_details`, `get_service_status`, `get_warranty_policy`)
# MAGIC 7. Grants to `account users` (USE CATALOG / USE SCHEMA / SELECT / EXECUTE)
# MAGIC 8. Governance: UC column mask on `repair_orders` PII (unmasked only for the admin group)
# MAGIC 9. A shared **PRO serverless SQL warehouse** granted to all users (for the agent's OBO lookup)
# MAGIC 10. A shared autoscaling **Lakebase** project (`agent-apps-memory`), `users` CAN MANAGE on it
# MAGIC     (so each student's app attaches it as a `postgres` resource — SP memory, per-app schema),
# MAGIC     + a `users`-group Postgres role (students browse memory in the end-of-lab beat)
# MAGIC 11. **Unity AI Gateway** config on the agent's LLM serving endpoint (guardrails: PII + safety,
# MAGIC     inference-table payload logging, usage tracking, per-user rate limit) — governance for the
# MAGIC     model path, complementing the OBO + UC column mask on the data path
# MAGIC 12. **AI Dev Kit skills** distributed to `/Workspace/.assistant/skills/` so every student's
# MAGIC     **Genie Code** can scaffold + deploy the agent App (Module 2) and evaluate it (Module 5)
# MAGIC 13. A shared **lab-guide app** (`agent-lab-guide`) — the participant guide + field-guide deck,
# MAGIC     deployed once per workspace with `users` CAN_USE
# MAGIC
# MAGIC **The scenario:** a **General Motors dealer service assistant** — GM vehicles across Chevrolet,
# MAGIC GMC, Buick, and Cadillac; repair orders; warranty / recall / service policies; and vehicle
# MAGIC brochures. Runs `spark.sql()` on UC-enabled serverless compute, so no warehouse discovery /
# MAGIC SQL-Execution-API is needed for provisioning.

# COMMAND ----------

# MAGIC %md ## Config & authentication

# COMMAND ----------

import csv
import datetime
import io
import time

# Framework injects host/token as base_parameters (see dbacademy.run_setup). Widgets with empty
# defaults so the notebook is also runnable interactively (falls back to notebook auth).
dbutils.widgets.text("host", "", "Workspace URL (injected)")
dbutils.widgets.text("token", "", "Admin token (injected)")
dbutils.widgets.text("workshop_catalog", "agent_apps_workshop", "Workshop catalog")
dbutils.widgets.text("workshop_schema", "shared", "Shared schema")
dbutils.widgets.text("vs_endpoint", "agent-apps-vs", "Vector Search endpoint")
dbutils.widgets.text("admin_group", "admins", "Admin group (unmasked PII)")

HOST = dbutils.widgets.get("host").strip()
TOKEN = dbutils.widgets.get("token").strip()
CATALOG = dbutils.widgets.get("workshop_catalog").strip()
SCHEMA = dbutils.widgets.get("workshop_schema").strip()
VS_ENDPOINT = dbutils.widgets.get("vs_endpoint").strip()
ADMIN_GROUP = dbutils.widgets.get("admin_group").strip()

# Workspace-local group for resource-level permission APIs (warehouses, workspace ACLs). Use the
# built-in workspace `users` group — NOT `account users`: the account group resolves for UC SQL
# grants (via the metastore) but the workspace Permissions API only sees workspace-local groups,
# so `account users` errors with "does not exist"/"not found".
# (The framework's own warehouse/VS grants use `users` too.)
ALL_USERS_GROUP = "users"

# Columns in `repair_orders` that contain customer PII and get masked for non-admins
PII_COLUMNS = ["customer_email", "customer_address"]

from databricks.sdk import WorkspaceClient

# Prefer injected creds (privileged setup identity); fall back to notebook-native auth.
if HOST and TOKEN:
    w = WorkspaceClient(host=HOST, token=TOKEN)
else:
    w = WorkspaceClient()

print(f"Workspace: {w.config.host}")
print(f"Catalog:   {CATALOG}.{SCHEMA}")
print(f"VS:        {VS_ENDPOINT}  |  Admin group: {ADMIN_GROUP}")
print("Scenario:  General Motors dealer service assistant")

# COMMAND ----------

# MAGIC %md ## Embedded source data (self-contained — no repo/container filesystem dependency)

# COMMAND ----------

VEHICLES_CSV = """vehicle_id,model_name,brand,category,model_year,msrp,basic_warranty_years,availability,open_recall,description
CHV-SIL,Chevrolet Silverado 1500,Chevrolet,Trucks,2024,36500,3,In Production,"N242178000 - Rearview camera image may not display on the center screen; dealers will update the software free of charge.","Full-size pickup with available 6.2L V8, up to 13,300 lbs max towing, and a 13.4-inch infotainment display. Crew, double, and regular cab configurations."
CHV-EQXEV,Chevrolet Equinox EV,Chevrolet,Electric SUVs,2025,34995,3,In Production,None,"Affordable electric SUV with up to 319 miles of EPA-estimated range, DC fast charging, and available Super Cruise. Roomy five-seat interior."
CHV-BLZEV,Chevrolet Blazer EV,Chevrolet,Electric SUVs,2025,44995,3,In Production,None,"Midsize electric SUV with up to 334 miles of range, available AWD, and an 11-inch driver display paired with a 17.7-inch touchscreen."
CHV-CORV,Chevrolet Corvette Stingray,Chevrolet,Sports Cars,2024,68300,3,In Production,None,"Mid-engine sports car with a 6.2L V8 producing 495 hp, 0-60 in under 3 seconds, and a removable roof panel."
CHV-CAM,Chevrolet Camaro,Chevrolet,Sports Cars,2024,31500,3,Discontinued,None,"Iconic rear-wheel-drive sports coupe. Final model-year production has ended and the Camaro nameplate is being retired; remaining units are limited to existing dealer stock."
CHV-MAL,Chevrolet Malibu,Chevrolet,Sedans,2024,26100,3,Discontinued,None,"Midsize sedan with a turbocharged engine and roomy cabin. This nameplate has been discontinued as Chevrolet shifts its lineup toward trucks, SUVs, and EVs."
CHV-TRAX,Chevrolet Trax,Chevrolet,SUVs,2025,21495,3,In Production,None,"Value-priced subcompact SUV with a spacious interior, an 11-inch touchscreen, and standard safety features."
CHV-TAH,Chevrolet Tahoe,Chevrolet,SUVs,2024,58200,3,In Production,None,"Full-size three-row SUV with an available diesel powertrain, up to 8,400 lbs towing, and seating for up to nine."
GMC-SIE,GMC Sierra 1500,GMC,Trucks,2024,39500,3,In Production,None,"Full-size pickup with an available AT4X off-road package, premium interior, and the MultiPro tailgate. Up to 13,200 lbs max towing."
GMC-YUK,GMC Yukon,GMC,SUVs,2024,62400,3,In Production,None,"Full-size SUV with an available Denali Ultimate trim, air ride adaptive suspension, and a 16.8-inch diagonal display."
GMC-HUM,GMC Hummer EV Pickup,GMC,Electric Trucks,2025,98845,3,In Production,None,"All-electric supertruck with up to 1,000 hp, CrabWalk, Extract Mode, and removable Infinity Roof panels."
GMC-ACA,GMC Acadia,GMC,SUVs,2025,43000,3,In Production,None,"Midsize three-row SUV, larger for 2025, with a standard 2.5L turbo engine and a 15-inch touchscreen."
BUI-ENC,Buick Enclave,Buick,SUVs,2025,45500,3,In Production,None,"Three-row luxury SUV with a redesigned cabin, available Super Cruise, and a 30-inch diagonal display."
BUI-EGX,Buick Encore GX,Buick,SUVs,2025,26900,3,In Production,None,"Subcompact luxury SUV with turbocharged efficiency, a quiet cabin, and available all-wheel drive."
BUI-ENV,Buick Envista,Buick,SUVs,2024,23800,3,In Production,None,"Sporty, low-slung compact SUV with striking design, an 11-inch touchscreen, and standard driver-assistance features."
CAD-ESC,Cadillac Escalade,Cadillac,SUVs,2025,87595,3,In Production,None,"Flagship full-size luxury SUV with a 55-inch curved LED display, available Super Cruise, and a 6.2L V8 or diesel. Backed by Cadillac's exclusive 6-year/72,000-mile comprehensive bumper-to-bumper coverage for total peace of mind."
CAD-LYR,Cadillac LYRIQ,Cadillac,Electric SUVs,2025,58590,3,In Production,None,"All-electric luxury SUV with up to 326 miles of range, a 33-inch LED display, and available Super Cruise. Covered by Cadillac's exclusive 6-year/72,000-mile comprehensive warranty."
CAD-CT5,Cadillac CT5,Cadillac,Sedans,2024,39990,3,In Production,None,"Rear-wheel-drive luxury sport sedan with an available 3.0L twin-turbo V6 and a refreshed 33-inch curved display."
CAD-XT5,Cadillac XT5,Cadillac,SUVs,2024,45590,3,In Production,None,"Midsize luxury SUV with a spacious cabin, available AWD, and a suite of driver-assistance technologies."
"""

REPAIR_ORDERS_CSV = """ro_number,customer_id,customer_email,vin,model_name,service_date,status,customer_address,service_type,total_cost
RO-10001,CUST-001,alice.johnson@email.com,1GCUYDED5RZ143092,Chevrolet Silverado 1500,2025-08-15,Completed,"123 Main St, Detroit MI 48201",Oil change and tire rotation,89.95
RO-10002,CUST-002,bob.smith@email.com,3GKALMEV8RL284471,GMC Acadia,2025-08-18,Completed,"456 Oak Ave, Warren MI 48089",Brake pad replacement,412.50
RO-10003,CUST-003,carol.white@email.com,1G6DW5RK9R0134882,Cadillac CT5,2025-08-20,Completed,"789 Pine Rd, Royal Oak MI 48067",Multi-point inspection,0.00
RO-10004,CUST-004,david.brown@email.com,1GNSKCKD2RR201765,Chevrolet Tahoe,2025-09-01,Completed,"321 Elm St, Troy MI 48083",Transmission fluid service,289.99
RO-10005,CUST-005,emma.davis@email.com,1GYKNDRS8RZ118340,Cadillac XT5,2025-09-05,Completed,"654 Maple Dr, Dearborn MI 48124",Infotainment software update,0.00
RO-10006,CUST-006,frank.miller@email.com,1GT49REY4RF119927,GMC Sierra 1500,2025-09-10,In Service,"987 Cedar Ln, Livonia MI 48150",A/C system repair,318.75
RO-10007,CUST-007,grace.wilson@email.com,3GNKBHR40RS557413,Chevrolet Blazer EV,2025-09-12,Awaiting Parts,"147 Birch Blvd, Ann Arbor MI 48103",High-voltage battery diagnostic,0.00
RO-10008,CUST-008,henry.moore@email.com,1G4GA5AR5RF142208,Buick Enclave,2025-09-15,Completed,"258 Walnut Way, Sterling Heights MI 48310",Wheel alignment,149.95
RO-10009,CUST-009,iris.taylor@email.com,1GNEVGKW2RJ166554,Chevrolet Equinox EV,2025-09-18,Scheduled,"369 Spruce St, Novi MI 48375",Tire rotation and software update,0.00
RO-10010,CUST-010,jack.anderson@email.com,1G6DN5RK0R0140097,Cadillac CT5,2025-09-20,Scheduled,"741 Willow Ave, Farmington Hills MI 48334","30,000-mile scheduled maintenance",349.00
RO-10011,CUST-001,alice.johnson@email.com,1GCUYDED5RZ143092,Chevrolet Silverado 1500,2025-09-22,Scheduled,"123 Main St, Detroit MI 48201",Recall repair - rearview camera software,0.00
RO-10012,CUST-011,karen.thomas@email.com,1GYFZCR46RF528813,Cadillac LYRIQ,2025-09-24,In Service,"852 Ash Ct, Grand Rapids MI 49503",Charging port diagnostic,0.00
RO-10013,CUST-012,liam.jackson@email.com,1GKS1BKL3RR330486,GMC Yukon,2025-09-25,Completed,"963 Poplar Pl, Lansing MI 48910",Brake fluid flush,159.99
RO-10014,CUST-013,mia.harris@email.com,KL4MMDS26RB075512,Buick Encore GX,2025-09-26,Completed,"174 Magnolia Rd, Kalamazoo MI 49001",Oil change,64.95
RO-10015,CUST-014,noah.martin@email.com,1GC4YUEY8RF210338,Chevrolet Silverado 1500,2025-09-28,Awaiting Parts,"285 Chestnut St, Flint MI 48502",Suspension repair,742.00
RO-10016,CUST-015,olivia.garcia@email.com,1GYKPMRS4RZ204119,Cadillac XT5,2025-09-29,Completed,"396 Dogwood Dr, Pontiac MI 48342",Cabin air filter and inspection,89.50
RO-10017,CUST-016,peter.martinez@email.com,3GTUUCED5RG223740,GMC Sierra 1500,2025-10-01,Scheduled,"507 Hickory Ln, Southfield MI 48075",Powertrain warranty repair,0.00
RO-10018,CUST-017,quinn.robinson@email.com,1GNERGKW4RJ188265,Chevrolet Equinox EV,2025-10-02,In Service,"618 Cypress Ave, Rochester MI 48307",Software recalibration,0.00
RO-10019,CUST-018,rachel.clark@email.com,1G4PR5SK6R4102938,Buick Envista,2025-10-03,Completed,"729 Juniper Blvd, Kentwood MI 49512",Tire replacement (set of 4),893.20
RO-10020,CUST-019,sam.lewis@email.com,1GYFZDR47RF530221,Cadillac LYRIQ,2025-10-04,Scheduled,"830 Sequoia Trl, Midland MI 48640",First scheduled EV service,0.00
"""

POLICIES_CSV = """policy,policy_details,last_updated
new_vehicle_warranty,"Every new GM vehicle includes a 3-year/36,000-mile (whichever comes first) bumper-to-bumper New Vehicle Limited Warranty covering defects in materials and workmanship, plus a 5-year/60,000-mile Powertrain Limited Warranty. Coverage begins on the date the vehicle is first delivered. It does not cover normal wear, maintenance items, or damage from misuse or accidents.",2025-01-15
recall_policy,"Safety recall repairs are always performed free of charge at any authorized GM dealer, regardless of the vehicle's age, mileage, coverage status, or ownership history. Provide your 17-character VIN to check for open recalls and to schedule the free repair.",2025-01-15
goodwill_policy,"Goodwill assistance for repairs outside the vehicle's factory coverage is considered on a case-by-case basis, requires documented service history and service manager approval, and is not guaranteed. Advisors follow published goodwill guidelines; there is no blanket entitlement to free post-coverage repairs.",2025-01-15
roadside_assistance,"A 5-year/60,000-mile Roadside Assistance program is included with every new GM vehicle: 24/7 towing to the nearest dealer, flat-tire service, jump-starts, lockout service, and emergency fuel delivery.",2025-01-15
service_scheduling,"Service appointments can be booked online, in your brand's mobile app, or by calling your dealer. Most routine maintenance (oil changes, tire rotation, multi-point inspection) is available same-day or as a walk-in. Complimentary loaner or shuttle service is offered at participating dealers.",2025-01-15
gm_financial_policy,"Financing and leasing are offered through GM Financial for qualified buyers. Rates and terms depend on credit, term length, and current regional offers. Existing account questions and payoff quotes are handled by GM Financial directly.",2025-01-15
parts_return_policy,"Unused, uninstalled GM Genuine Parts and ACDelco parts may be returned within 30 days with the original receipt and packaging. Electrical components and special-order parts are non-returnable once opened. Installed parts are covered by the parts warranty, not the return policy.",2025-01-15
privacy_policy,"GM collects only the data needed to service your vehicle and support your account. We do not sell personal data to third parties. You may request deletion of your data at any time by contacting privacy@gm.com.",2025-01-15
"""

# COMMAND ----------

# MAGIC %md ## Step 1 — Catalog & schema

# COMMAND ----------

spark.sql(f"CREATE CATALOG IF NOT EXISTS `{CATALOG}`")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`")
print(f"  Catalog and schema ready: {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md ## Step 2 — Load source tables from embedded CSV (idempotent)

# COMMAND ----------

from pyspark.sql.types import StructType, StructField, StringType


def _fqn(table):
    return f"`{CATALOG}`.`{SCHEMA}`.`{table}`"


def load_table(table, csv_text):
    """Create + load a table from embedded CSV text if it doesn't exist or is empty. All STRING cols."""
    dotted = f"{CATALOG}.{SCHEMA}.{table}"
    if spark.catalog.tableExists(dotted):
        n = spark.table(_fqn(table)).count()
        if n > 0:
            print(f"  {table:<14} already has {n} rows — skipping")
            return
    reader = csv.reader(io.StringIO(csv_text.strip()))
    rows = list(reader)
    header, data = rows[0], [tuple(r) for r in rows[1:]]
    schema = StructType([StructField(c, StringType(), True) for c in header])
    df = spark.createDataFrame(data, schema)
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(dotted)
    print(f"  {table:<14} loaded {df.count()} rows")


load_table("vehicles", VEHICLES_CSV)
load_table("repair_orders", REPAIR_ORDERS_CSV)
load_table("policies", POLICIES_CSV)

# COMMAND ----------

# MAGIC %md ## Step 3 — Derive `vehicle_docs` (Vector Search source, CDF enabled)

# COMMAND ----------

docs_dotted = f"{CATALOG}.{SCHEMA}.vehicle_docs"
if spark.catalog.tableExists(docs_dotted) and spark.table(_fqn("vehicle_docs")).count() > 0:
    print("  vehicle_docs already populated — skipping")
else:
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {_fqn('vehicle_docs')}
        TBLPROPERTIES (delta.enableChangeDataFeed = true)
        AS SELECT
            vehicle_id,
            model_name,
            brand,
            category AS vehicle_category,
            CONCAT(
                model_year, ' ', model_name, ' | Brand: ', brand,
                ' | Category: ', category,
                ' | MSRP: $', msrp,
                ' | Availability: ', availability,
                ' | Warranty: ', basic_warranty_years, ' year(s). ',
                description
            ) AS indexed_doc
        FROM {_fqn('vehicles')}
    """)
    print(f"  vehicle_docs created: {spark.table(_fqn('vehicle_docs')).count()} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 4 — Inject the 3 workshop quality bugs
# MAGIC 1. Discontinued vehicles described as "available to order" in `vehicle_docs`
# MAGIC 2. Cadillac brochures claim a 6-year/72,000-mile warranty (the catalog `basic_warranty_years`
# MAGIC    AND the official policy both say 3-year/36,000-mile — ONLY the marketing doc lies, so the
# MAGIC    "ground in authoritative sources" lesson is unambiguous even to sharp-eyed attendees)
# MAGIC 3. An over-permissive "extended" loyalty service policy row

# COMMAND ----------

docs_t = _fqn("vehicle_docs")
pol_t = _fqn("policies")

# BUG 1: discontinued vehicles labelled as available to order
disc = spark.sql(f"""
    SELECT vehicle_id FROM {_fqn('vehicles')}
    WHERE LOWER(availability) = 'discontinued' LIMIT 5
""").collect()
if disc:
    ids = ", ".join(f"'{r['vehicle_id']}'" for r in disc)
    spark.sql(f"""
        UPDATE {docs_t}
        SET indexed_doc = CONCAT(indexed_doc,
            ' Great news — this model is still available to order today for immediate delivery.',
            ' Visit your dealer or reserve online now.')
        WHERE vehicle_id IN ({ids})
    """)
    print(f"  Bug 1: marked {len(disc)} discontinued vehicles as available to order")
else:
    print("  Bug 1: no discontinued vehicles found — skipping")

# BUG 2: wrong warranty duration in Cadillac brochures.
# Cadillac is the premium brand whose marketing brags about "comprehensive" coverage, so the lie
# lives there. Applying to every Cadillac doc makes the pick deterministic and guarantees the
# Cadillac Escalade (the vehicle Modules 4/5 ask about) is included. 6 years is chosen so it can't
# be mistaken for GM's real 5-year/60,000-mile powertrain term — the warranty judge stays clean.
cadillac = spark.sql(f"""
    SELECT vehicle_id FROM {docs_t} WHERE LOWER(brand) = 'cadillac'
""").collect()
if cadillac:
    ids = ", ".join(f"'{r['vehicle_id']}'" for r in cadillac)
    # Replace the generated (correct) warranty figure so the doc lies CONSISTENTLY at 6 years, then
    # append the marketing claim. vehicles.basic_warranty_years stays 3 (truth, matches policy).
    spark.sql(f"""
        UPDATE {docs_t}
        SET indexed_doc = CONCAT(
            REPLACE(indexed_doc, 'Warranty: 3 year(s)', 'Warranty: 6 year(s)'),
            ' Every Cadillac includes our exclusive 6-year/72,000-mile comprehensive',
            ' bumper-to-bumper limited warranty covering parts and labor.')
        WHERE vehicle_id IN ({ids})
    """)
    print(f"  Bug 2: injected wrong (6yr) warranty into {len(cadillac)} Cadillac docs (catalog/policy say 3)")
    # The lab's M4/M5 warranty question targets the Cadillac Escalade — assert its doc now lies.
    _esc = spark.sql(f"""
        SELECT indexed_doc FROM {docs_t} WHERE model_name LIKE '%Escalade%' LIMIT 1
    """).collect()
    assert _esc and "6 year" in _esc[0]["indexed_doc"], (
        "Bug 2 FAILED to land on the Cadillac Escalade doc — Modules 4/5 will not work. "
        f"Doc: {_esc[0]['indexed_doc'] if _esc else 'MISSING'}")
    print("  Bug 2: verified — Cadillac Escalade doc claims a 6-year warranty")
else:
    print("  Bug 2: no Cadillac vehicles found — skipping")

# BUG 3: over-permissive extended loyalty service policy (skip if already present).
# Deliberately avoids the word "warranty" so get_warranty_policy('warranty') stays clean for the
# warranty flip; it surfaces on repair / coverage / goodwill questions instead (the data bug).
existing = spark.sql(f"SELECT COUNT(*) AS c FROM {pol_t} WHERE LOWER(policy) LIKE '%extended%'").collect()
if existing and int(existing[0]["c"]) > 0:
    print("  Bug 3: extended loyalty service policy already present — skipping")
else:
    spark.sql(f"""
        INSERT INTO {pol_t} (policy, policy_details, last_updated)
        VALUES (
            'Customer Loyalty Service Policy (Extended)',
            'We value our loyal owners above all else. When a customer is unhappy with a repair or a bill, our service team is empowered to make it right. Advisors may authorize complimentary repairs for loyal customers even after their factory coverage has lapsed, at their own discretion, with no documentation or manager approval required. Exceptions can always be made for long-time GM owners and in cases of genuine hardship. Representatives should use their best judgment to ensure the customer leaves satisfied.',
            CAST(current_date() AS STRING)
        )
    """)
    print("  Bug 3: inserted over-permissive extended loyalty service policy")

print("  Quality bugs injected.")

# COMMAND ----------

# MAGIC %md ## Step 5 — Vector Search endpoint + delta-sync index

# COMMAND ----------

from databricks.sdk.service.vectorsearch import (
    EndpointType, VectorIndexType,
    DeltaSyncVectorIndexSpecRequest, EmbeddingSourceColumn, PipelineType,
)

# Endpoint (create + wait — index creation requires a ready endpoint)
try:
    ep = w.vector_search_endpoints.get_endpoint(VS_ENDPOINT)
    print(f"  VS endpoint '{VS_ENDPOINT}' exists "
          f"(state: {ep.endpoint_status.state if ep.endpoint_status else '?'})")
except Exception:
    print(f"  Creating VS endpoint '{VS_ENDPOINT}' (cold start can take 20-30 min)...")
    w.vector_search_endpoints.create_endpoint_and_wait(
        name=VS_ENDPOINT, endpoint_type=EndpointType.STANDARD,
        timeout=datetime.timedelta(minutes=40),
    )
    print("  VS endpoint ready.")

index_name = f"{CATALOG}.{SCHEMA}.vehicle_docs_vs"
source_table = f"{CATALOG}.{SCHEMA}.vehicle_docs"

index_exists = False
try:
    w.vector_search_indexes.get_index(index_name)
    index_exists = True
    print(f"  VS index '{index_name}' already exists — skipping create")
except Exception as e:
    if "detailed_state" in str(e):  # SDK attribute quirk when the index already exists
        index_exists = True
        print("  VS index exists (SDK attribute quirk) — skipping create")

if not index_exists:
    w.vector_search_indexes.create_index(
        name=index_name,
        endpoint_name=VS_ENDPOINT,
        primary_key="vehicle_id",
        index_type=VectorIndexType.DELTA_SYNC,
        delta_sync_index_spec=DeltaSyncVectorIndexSpecRequest(
            source_table=source_table,
            pipeline_type=PipelineType.TRIGGERED,
            embedding_source_columns=[
                EmbeddingSourceColumn(
                    name="indexed_doc",
                    embedding_model_endpoint_name="databricks-gte-large-en",
                )
            ],
        ),
    )
    print("  VS index creation triggered — it will continue syncing in the background.")
    # Bounded wait so the setup job doesn't run for 30+ min; the index syncs asynchronously.
    for attempt in range(20):  # ~10 min
        time.sleep(30)
        try:
            idx = w.vector_search_indexes.get_index(index_name)
            if idx.status and getattr(idx.status, "ready", False):
                n = getattr(idx.status, "indexed_row_count", "?")
                print(f"  VS index ready — {n} rows indexed.")
                break
            msg = (getattr(idx.status, "message", "") or "") if idx.status else ""
            print(f"  [{attempt + 1}/20] index syncing: {msg or 'in progress...'}")
        except Exception as e:
            print(f"  [{attempt + 1}/20] could not check index state yet: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 6 — UC function tools (the agent's structured-lookup tools)
# MAGIC Created in the shared schema so every student's agent reaches them via the managed MCP server at
# MAGIC `{host}/api/2.0/mcp/functions/agent_apps_workshop/shared`. The tool COMMENTs matter — the LLM
# MAGIC uses them to decide when to call each tool. (4th "tool" = the `vehicle_docs_vs` VS index.)

# COMMAND ----------

spark.sql(f"""
    CREATE OR REPLACE FUNCTION `{CATALOG}`.`{SCHEMA}`.get_vehicle_details(
      model_name STRING COMMENT 'Exact or partial GM vehicle model name to look up, e.g. "Silverado" or "Escalade"'
    )
    RETURNS STRING
    COMMENT 'Look up a GM vehicle by model name. Returns brand, model year, category, MSRP, availability (including whether the model is Discontinued), any open safety recall, and description. Call this before recommending or confirming any vehicle so you never describe a discontinued model as available, and to check for open recalls. Warranty, recall, and service terms are policy questions — use get_warranty_policy for those.'
    RETURN (
      SELECT CONCAT_WS('\\n',
          CONCAT('Vehicle: ', model_year, ' ', model_name), CONCAT('Brand: ', brand),
          CONCAT('Category: ', category), CONCAT('MSRP: $', msrp),
          CONCAT('Availability: ', availability), CONCAT('Open recall: ', open_recall),
          CONCAT('Description: ', description))
      FROM `{CATALOG}`.`{SCHEMA}`.vehicles
      WHERE lower(model_name) LIKE CONCAT('%', lower(get_vehicle_details.model_name), '%')
      LIMIT 1
    )
""")

spark.sql(f"""
    CREATE OR REPLACE FUNCTION `{CATALOG}`.`{SCHEMA}`.get_service_status(
      ro_identifier STRING COMMENT 'A repair order number (e.g. RO-10001) or a customer email address'
    )
    RETURNS STRING
    COMMENT 'Look up a service repair order by RO number or customer email. Returns service status, vehicle, VIN, service date, service type, cost, and the customer email and address. Customer PII (email, address) is protected by a Unity Catalog column mask, so values are redacted unless the caller is authorized.'
    RETURN (
      SELECT CONCAT_WS('\\n',
          CONCAT('Repair order: ', ro_number), CONCAT('Status: ', status),
          CONCAT('Vehicle: ', model_name), CONCAT('VIN: ', vin),
          CONCAT('Service date: ', service_date), CONCAT('Service: ', service_type),
          CONCAT('Customer email: ', customer_email), CONCAT('Customer address: ', customer_address),
          CONCAT('Total: $', total_cost))
      FROM `{CATALOG}`.`{SCHEMA}`.repair_orders
      WHERE ro_number = get_service_status.ro_identifier
         OR lower(customer_email) = lower(get_service_status.ro_identifier)
      LIMIT 1
    )
""")

spark.sql(f"""
    CREATE OR REPLACE FUNCTION `{CATALOG}`.`{SCHEMA}`.get_warranty_policy(
      topic STRING DEFAULT NULL COMMENT 'Optional policy category filter, e.g. "warranty", "recall", "roadside", "goodwill". Leave empty to get all policies.'
    )
    RETURNS STRING
    COMMENT 'Return GM''s official warranty, recall, and service policies. This is the SOURCE OF TRUTH for any question about warranty terms, recalls, roadside assistance, financing, or repair coverage — do not rely on vehicle brochures or descriptions for policy. Optionally filter by policy category.'
    RETURN (
      SELECT CONCAT_WS('\\n\\n', collect_list(CONCAT(policy, ': ', policy_details)))
      FROM `{CATALOG}`.`{SCHEMA}`.policies
      WHERE get_warranty_policy.topic IS NULL
         OR lower(policy) LIKE CONCAT('%', lower(get_warranty_policy.topic), '%')
         OR lower(policy_details) LIKE CONCAT('%', lower(get_warranty_policy.topic), '%')
    )
""")
print("  Created UC function tools: get_vehicle_details, get_service_status, get_warranty_policy")

# COMMAND ----------

# MAGIC %md ## Step 7 — Grants to `account users`

# COMMAND ----------

for stmt in [
    f"GRANT USE CATALOG ON CATALOG `{CATALOG}` TO `account users`",
    f"GRANT USE SCHEMA ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `account users`",
    f"GRANT SELECT ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `account users`",
    f"GRANT EXECUTE ON SCHEMA `{CATALOG}`.`{SCHEMA}` TO `account users`",
]:
    try:
        spark.sql(stmt)
    except Exception as e:
        print(f"  Grant skipped (non-fatal): {e}")
print("  Permissions granted.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 8 — Governance: UC column mask on `repair_orders` PII
# MAGIC Members of the admin group see real PII; everyone else (incl. ephemeral lab users and the
# MAGIC app service principal) sees `***REDACTED***`. This is the on-behalf-of-user story Module 3 teaches.
# MAGIC
# MAGIC `mask_pii` lives in a separate **`governance`** schema (NOT `shared`) so it does not surface as
# MAGIC a tool on the agent's functions MCP server (which is scoped to `shared`). UC allows a column
# MAGIC mask to reference a function in another schema by fully-qualified name.

# COMMAND ----------

GOV_SCHEMA = "governance"
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{GOV_SCHEMA}`")
mask_fn = f"`{CATALOG}`.`{GOV_SCHEMA}`.`mask_pii`"
spark.sql(f"""
    CREATE OR REPLACE FUNCTION {mask_fn}(val STRING)
    RETURNS STRING
    COMMENT 'Masks customer PII for anyone who is not a member of the workshop admin group.'
    RETURN CASE
        WHEN is_account_group_member('{ADMIN_GROUP}') OR is_member('{ADMIN_GROUP}') THEN val
        ELSE '***REDACTED***'
    END
""")
print(f"  Created masking function {mask_fn} (unmasked for group '{ADMIN_GROUP}')")

# Readers must be able to resolve + execute the mask fn when they query repair_orders.
for stmt in [
    f"GRANT USE SCHEMA ON SCHEMA `{CATALOG}`.`{GOV_SCHEMA}` TO `account users`",
    f"GRANT EXECUTE ON FUNCTION {mask_fn} TO `account users`",
]:
    try:
        spark.sql(stmt)
    except Exception as e:
        print(f"  Grant skipped (non-fatal): {e}")

orders_t = _fqn("repair_orders")
for col in PII_COLUMNS:
    try:
        spark.sql(f"ALTER TABLE {orders_t} ALTER COLUMN `{col}` SET MASK {mask_fn}")
        print(f"  Applied mask to repair_orders.{col}")
    except Exception as e:
        if "already" in str(e).lower() or "mask" in str(e).lower():
            print(f"  Mask on repair_orders.{col} already applied — skipping")
        else:
            print(f"  Could not apply mask to repair_orders.{col}: {e}")

print(f"  Governance configured: repair_orders PII masked for non-'{ADMIN_GROUP}' identities.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 9 — Shared SQL warehouse for all users
# MAGIC The agent's `get_service_status` runs on-behalf-of-user via the SQL Statement Execution API and
# MAGIC needs a warehouse students can `CAN_USE`. `workspace_init()` can delete the starter warehouse,
# MAGIC so (like bricks' `grant_manage_on_first_warehouse_to_all_users`) we ensure a PRO serverless
# MAGIC warehouse exists and grant `CAN_USE` to `account users`.

# COMMAND ----------

from databricks.sdk.service.sql import CreateWarehouseRequestWarehouseType
from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel

# Canonical name so the agent + students can DISCOVER the warehouse by name (no hardcoded id,
# portable across fresh workspaces). We always ensure a warehouse named exactly this exists.
SHARED_WAREHOUSE_NAME = "agent-apps-shared"
shared_wh = None
try:
    shared_wh = next((x for x in w.warehouses.list() if x.name == SHARED_WAREHOUSE_NAME), None)
    if shared_wh is None:
        print(f"  Creating PRO serverless warehouse '{SHARED_WAREHOUSE_NAME}'...")
        shared_wh = w.warehouses.create_and_wait(
            name=SHARED_WAREHOUSE_NAME, cluster_size="2X-Small",
            enable_serverless_compute=True,
            warehouse_type=CreateWarehouseRequestWarehouseType.PRO,
            max_num_clusters=1, auto_stop_mins=30,
        )
    print(f"  Using warehouse '{shared_wh.name}' ({shared_wh.id})")
    w.permissions.update(
        request_object_type="warehouses",
        request_object_id=shared_wh.id,
        access_control_list=[AccessControlRequest(
            group_name=ALL_USERS_GROUP, permission_level=PermissionLevel.CAN_USE)],
    )
    print(f"  Granted CAN_USE on '{shared_wh.name}' to '{ALL_USERS_GROUP}'")
except Exception as e:
    print(f"  Warehouse setup issue (non-fatal): {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 10 — Shared Lakebase project (agent conversation memory)
# MAGIC The shipped agent writes each chat transcript to this autoscaling Lakebase project using the
# MAGIC **documented Databricks Apps pattern**: each student's
# MAGIC app is created with a `postgres` RESOURCE attached, which auto-creates a Postgres role for
# MAGIC that app's service principal; transcripts land in a per-app schema the SP owns. Two
# MAGIC workspace-wide pieces make that possible for non-admin students:
# MAGIC 1. **`users` gets CAN MANAGE on the database project** (accepted trade-off — Apps resource
# MAGIC    attachment requires it; the agent degrades gracefully if the project is ever damaged).
# MAGIC 2. The `users` group keeps a Postgres role (OAuth + `DATABRICKS_SUPERUSER`) so students can
# MAGIC    browse ALL app schemas in the Lakebase SQL editor for the end-of-lab memory visit.
# MAGIC ⚠️ Do NOT create agent_* tables in `public` — search_path is `<schema>, public`, and foreign
# MAGIC public tables break the apps' per-schema create-if-missing.

# COMMAND ----------

LAKEBASE_PROJECT_ID = "agent-apps-memory"

from databricks.sdk.service.postgres import (  # noqa: E402
    Project, ProjectSpec, ProjectDefaultEndpointSettings,
    Role, RoleRoleSpec, RoleIdentityType, RoleAuthMethod, RoleMembershipRole,
    BranchStatusState, EndpointStatusState,
)

lakebase_branch_name = None
lakebase_conn_host = None
try:
    project_name = f"projects/{LAKEBASE_PROJECT_ID}"
    # 1. Ensure the autoscaling project exists (scale-to-zero floor keeps idle cost ~nil)
    try:
        project = w.postgres.get_project(name=project_name)
        print(f"  Project '{project_name}' already exists — skipping creation")
    except Exception:
        print(f"  Creating autoscaling Lakebase project '{project_name}' (0.5-2 CU)...")
        op = w.postgres.create_project(
            project=Project(spec=ProjectSpec(
                display_name="Agent Apps Workshop Memory",
                default_endpoint_settings=ProjectDefaultEndpointSettings(
                    autoscaling_limit_min_cu=0.5,
                    autoscaling_limit_max_cu=2.0,
                ),
            )),
            project_id=LAKEBASE_PROJECT_ID,
        )
        project = op.wait()
        print(f"  Created project '{project.name}'")

    # 2. Wait for the default branch + primary endpoint; capture the connection host
    branches = list(w.postgres.list_branches(parent=project.name))
    branch = next((b for b in branches if b.status and b.status.default),
                  branches[0] if branches else None)
    if branch is None:
        raise RuntimeError(f"No branches found under {project.name}")
    lakebase_branch_name = branch.name
    deadline = time.time() + 600
    while True:
        b = w.postgres.get_branch(name=lakebase_branch_name)
        b_state = b.status.current_state if b.status else None
        eps = list(w.postgres.list_endpoints(parent=lakebase_branch_name))
        ep = eps[0] if eps else None
        ep_state = ep.status.current_state if ep and ep.status else None
        if ep and ep.status and ep.status.hosts:
            lakebase_conn_host = ep.status.hosts.host
        print(f"  branch={b_state} endpoint={ep_state} host={lakebase_conn_host}")
        if (b_state == BranchStatusState.READY
                and ep_state in (EndpointStatusState.ACTIVE, EndpointStatusState.IDLE)
                and lakebase_conn_host):
            print("  Lakebase endpoint ready.")
            break
        if time.time() > deadline:
            print("  WARNING: timed out waiting for the Lakebase endpoint — it may still be provisioning.")
            break
        time.sleep(10)

    # 3. Map the workspace `users` group to a Postgres role (OAuth login + superuser) so every
    #    lab user — and therefore every student's OBO agent — can connect with no further grants.
    role_exists = False
    try:
        for r in w.postgres.list_roles(parent=lakebase_branch_name):
            pg_role = (r.status.postgres_role if r.status else None) or (r.spec.postgres_role if r.spec else None)
            if pg_role == ALL_USERS_GROUP:
                role_exists = True
                break
    except Exception as e:
        print(f"  Could not list existing roles (continuing): {e}")
    if role_exists:
        print(f"  Postgres role for '{ALL_USERS_GROUP}' already exists — skipping")
    else:
        op = w.postgres.create_role(parent=lakebase_branch_name, role=Role(spec=RoleRoleSpec(
            identity_type=RoleIdentityType.GROUP,
            postgres_role=ALL_USERS_GROUP,
            auth_method=RoleAuthMethod.LAKEBASE_OAUTH_V1,
            membership_roles=[RoleMembershipRole.DATABRICKS_SUPERUSER],
        )))
        created = op.wait()
        print(f"  Mapped '{ALL_USERS_GROUP}' -> Postgres role '{created.name}' (OAuth + DATABRICKS_SUPERUSER)")

    # 4. Grant `users` CAN MANAGE on the database project so every student can attach it as an
    #    app resource (the documented Apps+Lakebase pattern requires CAN MANAGE to attach; the
    #    resource then auto-creates each app SP's Postgres role — zero per-student grants).
    #    Permissions object type is `database-projects`.
    try:
        w.api_client.do(
            "PATCH",
            f"/api/2.0/permissions/database-projects/{LAKEBASE_PROJECT_ID}",
            body={"access_control_list": [
                {"group_name": ALL_USERS_GROUP, "permission_level": "CAN_MANAGE"},
            ]},
        )
        print(f"  Granted '{ALL_USERS_GROUP}' CAN_MANAGE on database project '{LAKEBASE_PROJECT_ID}'")
    except Exception as e:
        print(f"  *** CAN_MANAGE grant failed (students cannot attach the memory resource!): {e}")

    # 5. The lab depends on memory working — make the provision log definitive.
    assert lakebase_conn_host, "Lakebase endpoint host missing — agent memory will not work"
    print(f"  Lakebase ready: project={LAKEBASE_PROJECT_ID} branch=production host={lakebase_conn_host}")
except Exception as e:
    # Memory is a headline lab feature but the agent degrades gracefully without it, so a
    # Lakebase outage must not kill the whole provision. Loud in the log either way.
    print(f"  *** Lakebase provisioning issue (agent will run memoryless!): {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 11 — Unity AI Gateway on the agent's LLM serving endpoint
# MAGIC The one service-principal call the agent makes is the **LLM** (Foundation Model APIs,
# MAGIC pay-per-token). We govern that model path with **Unity AI Gateway** — the counterpart to the
# MAGIC OBO + UC column mask that governs the *data* path. On the endpoint the agent uses (`LLM_ENDPOINT`
# MAGIC in `agent/app.yaml`) we enable, via `PUT /api/2.0/serving-endpoints/{name}/ai-gateway`:
# MAGIC - **AI guardrails** — PII detection (mask) + safety on both request and response;
# MAGIC - **Inference table** — full request/response payloads logged to a UC Delta table in `shared`;
# MAGIC - **Usage tracking** — tokens / latency / cost to UC system tables;
# MAGIC - **Rate limit** — a per-endpoint cap (the cost-control story).
# MAGIC
# MAGIC Best-effort and **non-fatal**: usage tracking is on for FMAPI by default, and the lab runs even
# MAGIC if the shared endpoint declines custom config. ⚠️ A *system-managed* pay-per-token endpoint may
# MAGIC reject custom gateway config; if this step logs that, create a workspace-owned serving endpoint
# MAGIC routing to the SAME model (so the planted warranty bug is preserved) and point `LLM_ENDPOINT` at
# MAGIC it. Verify the exact `ai_gateway` field shape against current docs when re-provisioning.

# COMMAND ----------

# The endpoint the shipped agent points at (keep in sync with LLM_ENDPOINT in agent/app.yaml).
LLM_ENDPOINT_NAME = "databricks-gpt-5-4"
gateway_configured = False

# Full desired config; we fall back to a reduced config if the platform rejects any piece.
_gw_full = {
    "usage_tracking_config": {"enabled": True},
    "inference_table_config": {
        "enabled": True,
        "catalog_name": CATALOG,
        "schema_name": SCHEMA,
        "table_name_prefix": "gateway_llm",
    },
    "guardrails": {
        "input": {"pii": {"behavior": "MASK"}, "safety": True},
        "output": {"pii": {"behavior": "MASK"}, "safety": True},
    },
    "rate_limits": [{"calls": 1000, "renewal_period": "minute", "key": "endpoint"}],
}
# Reduced config drops rate_limits (the field whose shape varies most across releases).
_gw_reduced = {k: v for k, v in _gw_full.items() if k != "rate_limits"}

_gw_path = f"/api/2.0/serving-endpoints/{LLM_ENDPOINT_NAME}/ai-gateway"
for _label, _body in (("full", _gw_full), ("reduced (no rate limit)", _gw_reduced)):
    try:
        w.api_client.do("PUT", _gw_path, body=_body)
        gateway_configured = True
        print(f"  Unity AI Gateway configured on '{LLM_ENDPOINT_NAME}' ({_label}): "
              f"guardrails (PII+safety), inference table {CATALOG}.{SCHEMA}.gateway_llm_*, usage tracking.")
        break
    except Exception as e:  # noqa: BLE001 — non-fatal; the lab runs without custom gateway config
        print(f"  Gateway config attempt ({_label}) did not apply: {e}")
if not gateway_configured:
    print("  *** Unity AI Gateway custom config not applied (non-fatal). The shared pay-per-token "
          "endpoint may be system-managed. To deliver the full model-governance story, create a "
          "workspace-owned serving endpoint routing to the same model and set LLM_ENDPOINT to it. "
          "Usage tracking via system tables is still available for FMAPI by default.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 12 — Distribute AI Dev Kit skills to Genie Code (workspace-wide)
# MAGIC Uploads the public [AI Dev Kit](https://github.com/databricks-solutions/ai-dev-kit) skills to
# MAGIC `/Workspace/.assistant/skills/`, which **Genie Code reads natively for every workspace user** —
# MAGIC no per-user setup, no MCP-server config. This is what makes each student's Genie Code able to
# MAGIC scaffold + `databricks bundle deploy` the agent App in Module 2 (skills: `databricks-app-python`,
# MAGIC `databricks-bundles`, `databricks-model-serving`, `databricks-vector-search`) and evaluate it in
# MAGIC Module 5 (`agent-evaluation`). Inlined from the FE `mcp-ai-dev-kit` app's `_distribute_skills()`
# MAGIC (uses `import_(format=RAW)` — AUTO fails under `.assistant/`). Best-effort; non-fatal.

# COMMAND ----------

import base64 as _b64
import json as _json
import posixpath
import urllib.request
from databricks.sdk.service.workspace import ImportFormat

WORKSPACE_SKILLS_DIR = "/Workspace/.assistant/skills"
SKIP_SKILLS = {"TEMPLATE"}
AIDK = ("databricks-solutions", "ai-dev-kit", "main", "databricks-skills")  # owner, repo, ref, subpath
MLFLOW_BASE = "https://raw.githubusercontent.com/mlflow/skills/main"
MLFLOW_SKILLS = [
    "agent-evaluation", "instrumenting-with-mlflow-tracing", "mlflow-onboarding",
    "analyze-mlflow-trace", "retrieving-mlflow-traces",
]


def _gh_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "agent-apps-setup", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def _put_skill_file(target_path, content_bytes):
    parent = posixpath.dirname(target_path)
    try:
        w.workspace.mkdirs(parent)
    except Exception:
        pass
    w.workspace.import_(
        path=target_path,
        content=_b64.b64encode(content_bytes).decode(),
        format=ImportFormat.RAW,
        overwrite=True,
    )


skills_done = 0
try:
    owner, repo, ref, sub = AIDK
    tree = _json.loads(_gh_get(f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"))
    blobs = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob" and t["path"].startswith(sub + "/")]
    skill_names = sorted({p.split("/")[1] for p in blobs if len(p.split("/")) > 2})
    for skill in skill_names:
        if skill in SKIP_SKILLS:
            continue
        try:
            for p in [b for b in blobs if b.startswith(f"{sub}/{skill}/")]:
                rel = p[len(f"{sub}/{skill}/"):]
                if not rel or rel.startswith("."):
                    continue
                raw = _gh_get(f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{p}")
                _put_skill_file(f"{WORKSPACE_SKILLS_DIR}/{skill}/{rel}", raw)
            skills_done += 1
        except Exception as e:
            print(f"  skill '{skill}' skipped (non-fatal): {e}")
    for skill in MLFLOW_SKILLS:
        try:
            raw = _gh_get(f"{MLFLOW_BASE}/{skill}/SKILL.md")
            _put_skill_file(f"{WORKSPACE_SKILLS_DIR}/{skill}/SKILL.md", raw)
            skills_done += 1
        except Exception as e:
            print(f"  mlflow skill '{skill}' skipped (non-fatal): {e}")
    print(f"  Distributed {skills_done} skills to {WORKSPACE_SKILLS_DIR} (Genie Code reads these for all users)")
except Exception as e:
    print(f"  AI Dev Kit skills distribution issue (non-fatal — Genie Code still works, just less Databricks-aware): {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 12b — (removed) the lab context now ships as an attachable doc, not a skill
# MAGIC We originally tried to ship a lab-specific **Genie Code skill** with the lab-specific
# MAGIC context. Testing on a freshly-provisioned workspace (2026-06-09) proved Genie Code's skill
# MAGIC registry is a **curated Databricks allowlist** — a custom `SKILL.md` dropped into
# MAGIC `/Workspace/.assistant/skills/` (or a user's `~/.assistant/skills/`) does **not** register, no
# MAGIC matter the frontmatter, fresh thread, or `@`-mention (even some Databricks-authored skills present
# MAGIC on disk — `databricks-config`, `databricks-genie`, `databricks-jobs` — don't appear). So the
# MAGIC the lab context now ships as **`agent_apps_lab/LAB_CONTEXT.md`** in the participant's home (via
# MAGIC the lab content zip); Module 0 has them explore the preloaded AI Dev Kit skills, then attach
# MAGIC `LAB_CONTEXT.md` to their Genie Code session (Add context / `@LAB_CONTEXT.md`). The AI Dev Kit
# MAGIC workspace skills below (`databricks-apps-python`, `databricks-mlflow-evaluation`, …) carry the
# MAGIC mechanics. Nothing to distribute here.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 12c — GRANT students read + VERIFY (the critical fix)
# MAGIC The setup SP writes the skills, so by default ONLY the SP/admins can read them — a non-admin
# MAGIC **lab student** (and their Genie Code) sees an EMPTY skills dir. We must grant the `users` group
# MAGIC (all workspace users) **CAN_READ** on `/Workspace/.assistant` so every student's Genie Code can
# MAGIC load the skills. Then we list the dir + confirm the grant so the provisioning log is definitive.
# MAGIC NOTE: this cell runs as the privileged setup identity, so the *listing* proves the files exist;
# MAGIC the *grant* is what makes them visible to students.

# COMMAND ----------

from databricks.sdk.service.workspace import (
    WorkspaceObjectAccessControlRequest,
    WorkspaceObjectPermissionLevel,
)

ASSISTANT_DIR = "/Workspace/.assistant"  # grant on the parent so it covers skills/ (perms inherit)
try:
    _oid = w.workspace.get_status(ASSISTANT_DIR).object_id
    # update_ (merge) — adds the grant without clobbering the SP/admin ACL
    w.workspace.update_permissions(
        workspace_object_type="directories",
        workspace_object_id=str(_oid),
        access_control_list=[
            WorkspaceObjectAccessControlRequest(
                group_name="users",
                permission_level=WorkspaceObjectPermissionLevel.CAN_READ,
            )
        ],
    )
    print(f"  Granted 'users' CAN_READ on {ASSISTANT_DIR} (object_id={_oid}) — students' Genie Code can now read skills")
except Exception as e:
    print(f"  ⚠️  Could not grant read on {ASSISTANT_DIR} (non-fatal but students may not see skills): {e}")

skills_present = []
try:
    skills_present = sorted(o.path.rstrip("/").split("/")[-1] for o in w.workspace.list(WORKSPACE_SKILLS_DIR))
except Exception as e:
    print(f"  Could not list {WORKSPACE_SKILLS_DIR}: {e}")
print(f"  SKILLS CHECK — {len(skills_present)} AI Dev Kit skills present in {WORKSPACE_SKILLS_DIR}:")
print(f"    {skills_present}")
if not skills_present:
    print("  ⚠️  WARNING: NO skills distributed — verify the job has outbound GitHub access "
          "(api.github.com / raw.githubusercontent.com).")
elif "databricks-apps-python" not in skills_present:
    print("  ⚠️  WARNING: databricks-apps-python missing — check Step 12 distribution ran.")
else:
    print("  ✅ AI Dev Kit skills present and 'users' has read — students' Genie Code is equipped. "
          "(The lab context ships as agent_apps_lab/LAB_CONTEXT.md, attached per-session — not a skill.)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Step 13 — Deploy the shared lab-guide app (all students, CAN_USE)
# MAGIC One `agent-lab-guide` Databricks App per workspace, serving the participant guide at `/`
# MAGIC (screenshots inlined) and the field-guide slide deck at `/deck`. The app source ships inside
# MAGIC this setup folder (`guide_app/`, baked by `build_zips.sh`); we deploy it as the privileged
# MAGIC user and grant the workspace `users` group **CAN_USE** so every student can open it. Static
# MAGIC content only — no scopes, no data access. Non-fatal: the lab works without it.

# COMMAND ----------

GUIDE_APP_NAME = "agent-lab-guide"
guide_app_url = None
try:
    # The guide_app folder sits next to THIS notebook (shipped in the setup zip).
    _nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    _setup_dir = "/Workspace" + "/".join(_nb_path.split("/")[:-1])
    _guide_src = f"{_setup_dir}/guide_app"
    print(f"  Guide app source: {_guide_src}")

    # 1. Create the app (idempotent)
    try:
        w.api_client.do("POST", "/api/2.0/apps", body={
            "name": GUIDE_APP_NAME,
            "description": "Lab guide + field-guide deck for the Agent Apps workshop",
        })
        print(f"  Created app '{GUIDE_APP_NAME}'")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"  App '{GUIDE_APP_NAME}' already exists — continuing")
        else:
            raise

    # 2. Wait for compute ACTIVE
    deadline = time.time() + 600
    while True:
        app_info = w.api_client.do("GET", f"/api/2.0/apps/{GUIDE_APP_NAME}")
        state = (app_info.get("compute_status") or {}).get("state")
        if state == "ACTIVE":
            print("  Compute ACTIVE")
            break
        if time.time() > deadline:
            raise TimeoutError("guide app compute did not become ACTIVE")
        time.sleep(10)

    # 3. Deploy the source
    w.api_client.do("POST", f"/api/2.0/apps/{GUIDE_APP_NAME}/deployments",
                    body={"source_code_path": _guide_src})
    deadline = time.time() + 600
    while True:
        app_info = w.api_client.do("GET", f"/api/2.0/apps/{GUIDE_APP_NAME}")
        state = (app_info.get("app_status") or {}).get("state")
        if state == "RUNNING":
            guide_app_url = app_info.get("url")
            print(f"  Guide app RUNNING: {guide_app_url}")
            break
        if time.time() > deadline:
            raise TimeoutError("guide app did not reach RUNNING")
        time.sleep(10)

    # 4. Every student can open it — grant CAN_USE, then VERIFY IT STICKS.
    # The Apps control plane keeps reconciling state for a while after a deployment, and a grant
    # made in that window can be silently REVERTED even though the PATCH returns 200 and this log
    # says "granted". Re-applied state DOES stick once reconciliation has passed, so:
    # grant -> verify present -> require it to survive two consecutive checks 60s apart,
    # re-granting whenever it vanishes (bounded at ~8 minutes).
    def _grant_guide_acl():
        w.api_client.do("PATCH", f"/api/2.0/permissions/apps/{GUIDE_APP_NAME}", body={
            "access_control_list": [
                {"group_name": ALL_USERS_GROUP, "permission_level": "CAN_USE"}],
        })

    def _guide_acl_has_users() -> bool:
        try:
            acl = w.api_client.do("GET", f"/api/2.0/permissions/apps/{GUIDE_APP_NAME}")
        except Exception as acl_err:  # noqa: BLE001 — unreadable ACL counts as "not there yet"
            print(f"  ACL read failed ({acl_err}) — treating as missing")
            return False
        for entry in (acl.get("access_control_list") or []):
            if entry.get("group_name") != ALL_USERS_GROUP:
                continue
            perms = entry.get("all_permissions") or []
            if any(p.get("permission_level") == "CAN_USE" for p in perms):
                return True
            if entry.get("permission_level") == "CAN_USE":  # response-shape fallback
                return True
        return False

    _grant_guide_acl()
    print(f"  CAN_USE grant for '{ALL_USERS_GROUP}' submitted — verifying it sticks "
          f"(post-deploy reconciliation can revert it) …")
    _stable = 0
    _acl_deadline = time.time() + 8 * 60
    while _stable < 2:
        if _guide_acl_has_users():
            _stable += 1
            print(f"  ACL check {_stable}/2 OK: '{ALL_USERS_GROUP}' has CAN_USE on '{GUIDE_APP_NAME}'")
        else:
            _stable = 0
            if time.time() > _acl_deadline:
                raise TimeoutError(
                    f"CAN_USE on '{GUIDE_APP_NAME}' keeps disappearing (Apps reconciliation) — "
                    f"grant manually: PATCH /api/2.0/permissions/apps/{GUIDE_APP_NAME}")
            print("  ACL check: grant MISSING (reconciliation reverted it) — re-granting …")
            _grant_guide_acl()
        if _stable < 2:
            time.sleep(60)
    print(f"  Granted CAN_USE on '{GUIDE_APP_NAME}' to '{ALL_USERS_GROUP}' — VERIFIED STABLE (2 checks, 60s apart)")
except Exception as e:
    print(f"  *** Guide app deployment issue (non-fatal — lab works without it): {e}")

# COMMAND ----------

# MAGIC %md ## Summary

# COMMAND ----------

print("=" * 64)
print("WORKSPACE SETUP COMPLETE")
print("=" * 64)
print(f"  Scenario:   General Motors dealer service assistant")
print(f"  Catalog:    {CATALOG}")
print(f"  Schema:     {CATALOG}.{SCHEMA}  (vehicles, repair_orders, policies, vehicle_docs)")
print(f"  VS Index:   {CATALOG}.{SCHEMA}.vehicle_docs_vs")
print(f"  Admin grp:  {ADMIN_GROUP} (unmasked PII)")
print(f"  Tools:      {CATALOG}.{SCHEMA}.{{get_vehicle_details, get_service_status, get_warranty_policy}}")
print(f"  Warehouse:  {getattr(shared_wh, 'name', 'pending')} "
      f"({getattr(shared_wh, 'id', '?')}, CAN_USE for all users)")
print(f"  Lakebase:   project=agent-apps-memory branch=production "
      f"host={lakebase_conn_host or 'MISSING — agent runs memoryless'} "
      f"(users CAN_MANAGE for app resource attach + group role for browsing)")
print(f"  AI Gateway: {LLM_ENDPOINT_NAME} -> "
      f"{'configured (guardrails + inference table + usage tracking)' if gateway_configured else 'custom config NOT applied (non-fatal — see Step 11)'}")
print(f"  Guide app:  {GUIDE_APP_NAME} -> {guide_app_url or 'FAILED (non-fatal)'} (users CAN_USE)")
print(f"  Genie Code: {len(skills_present)} AI Dev Kit skills at {WORKSPACE_SKILLS_DIR} "
      f"(users CAN_READ granted). Lab context → agent_apps_lab/LAB_CONTEXT.md (attach per-session).")
print()
print("  Agent env (resolved by name at runtime — see agent/app.py):")
print(f"    WORKSHOP_CATALOG={CATALOG}")
print(f"    WORKSHOP_SCHEMA={SCHEMA}")
print(f"    WORKSHOP_VS_INDEX=vehicle_docs_vs")
print(f"    LLM_ENDPOINT={LLM_ENDPOINT_NAME}")
if getattr(shared_wh, 'id', None):
    print(f"    WAREHOUSE_ID={shared_wh.id}")
