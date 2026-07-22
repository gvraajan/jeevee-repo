# GCP Storage Advisor — Knowledge Builder

Generates the three JSON artifacts that the Cloud Run recommendation engine
consumes. Runs on your laptop; you upload the output to GCS manually.

```
output/catalog.json     knowledge base (regions, zones, machine families/types, disk types, compatibility)
output/rules.json       data-driven recommendation rules
output/questions.json   the questionnaire (with dependencies + dynamic options)
```

## Why this architecture

GCP knowledge splits into two kinds, and the design mirrors that split:

| Source | What it provides | Where it lives |
|---|---|---|
| **Compute API** | What *exists*: regions, zones, machine types, disk-type names | `collectors/` (live) or `data/offline_snapshot.json` |
| **Curated overlay** | What things *can do*: disk performance, boot/regional/multi-writer eligibility, per-family disk support, recommendation rules, questionnaire | `knowledge/*.yaml` |

No API exposes disk↔machine-family compatibility, Hyperdisk limits, or
recommendation logic — so those are **human-curated YAML**, version-controlled,
each fact carrying a `source:` URL and `as_of:` date. We deliberately do **not**
scrape cloud.google.com (brittle, ToS-grey). When Google ships something new you
edit one YAML file and regenerate — Cloud Run needs no code change.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`google-cloud-compute` is only needed for `--live` mode.

## Run

```bash
# Offline (uses the bundled snapshot — no GCP credentials needed)
python builder.py

# Live (real Compute API — needs Application Default Credentials)
gcloud auth application-default login
python builder.py --live --project YOUR_PROJECT

# Limit live collection to a few regions (faster dev runs)
python builder.py --live --project YOUR_PROJECT --regions us-central1 europe-west1

# Regenerate just one artifact
python builder.py --only rules
```

Then review `output/` and upload to your bucket:

```bash
gsutil cp output/*.json gs://YOUR_BUCKET/knowledge/
```

## Project layout

```
builder.py               orchestrator + CLI
config.py                versions, paths, logging, runtime settings
models.py                Pydantic v2 schema (the contract with Cloud Run)
collectors/
  compute.py             ONLY module that knows live-vs-offline
  regions.py             regions + zones
  machine_types.py       machine types + family grouping
  disks.py               disk-type names + sizes
  documentation.py       loads the curated overlay (no scraping)
  compatibility.py       fast lookup index over the overlay
generators/
  catalog.py             merges API skeleton + overlay -> Catalog
  rules.py               validates + emits rules
  questions.py           validates + emits questionnaire
knowledge/
  compatibility.yaml     disk types + machine-family support matrix  ← maintain
  rules.yaml             recommendation rules                        ← maintain
  questions.yaml         questionnaire                               ← maintain
data/offline_snapshot.json   stand-in Compute API data
output/                  generated JSON (git-ignored in real use)
logs/                    per-run logs
```

## Rule & condition grammar

Rules are pure data. The engine walks each rule's `when` tree against the user's
answers; matching rules compete by `priority` (higher wins).

```yaml
when:
  all:
    - {field: workload, op: in, value: [sql_server, oracle]}
    - {field: environment, op: eq, value: production}
recommend:
  disk_type: hyperdisk-balanced
  confidence: high
  rationale: "..."
  alternatives: [hyperdisk-extreme]
```

Operators: `eq ne in not_in gte lte gt lt exists contains`.
Groups (`all` / `any`) nest arbitrarily. `field` must match a question's
`maps_to`.

## Validation guarantees

Every artifact is built through the Pydantic models in `models.py`, so the build
**fails loudly** on bad curated data instead of shipping it to Cloud Run:

- unknown operators, missing required fields, and typos (`extra="forbid"`) are rejected;
- duplicate rule/question IDs abort the build;
- `depends_on` fields referencing unknown questions log a warning;
- disk types present in the API but missing from the overlay emit a minimal entry
  **and** a warning telling you to curate them.

## ⚠️ Verify the seeded numbers

The performance ceilings, size ranges, and family support in
`knowledge/compatibility.yaml` are **seeded from best knowledge and must be
verified** against current Google Cloud docs before production use. Each entry
has a `source:` link for exactly that. This is the file you own.

## Versioning

- `BUILDER_VERSION` (config.py) — bump on code changes affecting output.
- `KNOWLEDGE_VERSION` (config.py) — bump when curated YAML changes. Surfaced in
  the Cloud Run UI as the knowledge-base version.
- `DOCUMENTATION_AS_OF` — the "Based on Google Cloud documentation as of …" line.

## Roadmap

- **Phase 1 (done):** full project skeleton; catalog/rules/questions all generate
  and validate offline.
- **Phase 2:** wire `--live` against a real project; reconcile API disk/machine
  availability per region into the catalog.
- **Phase 3:** the Cloud Run engine — load JSON from GCS, render questions,
  evaluate rules, return recommendations with rationale + sources.
- **Phase 4:** a `validate` subcommand and CI check that lints the overlay
  (dangling disk references, families citing unknown disks, stale `as_of`).
```
