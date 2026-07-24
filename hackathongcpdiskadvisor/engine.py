"""PD vs Hyperdisk Advisor Engine — V6.0

Compatible with your existing app.py and main.py. The public surface is
unchanged: RULES, WorkloadInput, recommend(), CANONICAL_ORDER,
DISK_CAPABILITIES, _eval_condition, and every key of the recommend() result
dict behave as before unless listed under "Fixes in V6" below.

Design goals (unchanged from V5):
- Keep hard constraints separate from scoring preferences.
- Gate candidates that cannot meet explicit IOPS / throughput / capacity.
- Avoid generic rules overpowering explicit high-performance requirements.
- Improve confidence so the winner is not always shown as 100%.

Fixes in V6:
- F1 (correctness, high severity): the fallback path could resurrect a disk
  that had been excluded for physically failing the IOPS/throughput/capacity
  requirement. A 500,000 IOPS request could be answered with "PD Balanced"
  (80,000 IOPS max). Capability exclusions are now permanent and the engine
  reports infeasibility instead of guessing.
- F2 (correctness, high severity): "ne" and numeric conditions fired on
  missing input, so a half-filled form scored rules it had no evidence for.
  Missing values now fail every comparison rather than defaulting.
- F3 (latent crash): _tie_break_key raised IndexError on a rule whose
  classification was blank.
- F4 (calibration): confidence mixed rule density with rule separation, so
  workloads matching many stacking rules reported 95% regardless of how
  close the runner-up was. Now driven by relative margin.
- F5 (diagnostics): rule table is validated at import; structurally
  unreachable disk types are reported in RULE_HEALTH.

Behaviour deliberately NOT changed: the scoring points, thresholds and disk
capability numbers are left exactly as they were. They encode product
decisions this refactor has no basis to overrule. See RULE_HEALTH and the
notes at the bottom of this file for the ones worth revisiting.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ── Constants ────────────────────────────────────────────────────────────────

DISK_TYPES = [
    "PD Balanced",
    "PD SSD",
    "PD Extreme",
    "Hyperdisk Balanced",
    "Hyperdisk Balanced HA",
    "Hyperdisk Throughput",
    "Hyperdisk Extreme",
    "Hyperdisk ML",
]

CANONICAL_ORDER = [
    "Hyperdisk Balanced HA",
    "Hyperdisk Extreme",
    "Hyperdisk Balanced",
    "Hyperdisk Throughput",
    "Hyperdisk ML",
    "PD Extreme",
    "PD SSD",
    "PD Balanced",
]

CLASSIFICATION_ORDER = {"VERIFIED": 2, "CERTIFIED": 2, "ARCHITECTURAL": 1, "FALLBACK": 0}

# Conservative single-volume limits used by the selector.
# Actual achievable performance still depends on VM machine series / vCPU count.
DISK_CAPABILITIES = {
    "PD Balanced": {"max_iops": 80000, "max_throughput": 1200, "max_capacity": 65536},
    "PD SSD": {"max_iops": 100000, "max_throughput": 1200, "max_capacity": 65536},
    "PD Extreme": {"max_iops": 120000, "max_throughput": 4000, "max_capacity": 65536},
    "Hyperdisk Balanced": {"max_iops": 160000, "max_throughput": 2400, "max_capacity": 65536},
    "Hyperdisk Balanced HA": {"max_iops": 100000, "max_throughput": 2400, "max_capacity": 65536},
    "Hyperdisk Throughput": {"max_iops": 120000, "max_throughput": 4000, "max_capacity": 65536},
    "Hyperdisk Extreme": {"max_iops": 350000, "max_throughput": 5000, "max_capacity": 65536},
    "Hyperdisk ML": {"max_iops": 120000, "max_throughput": 1200, "max_capacity": 65536},
}

# Static constraint map: zero-bypass dictionary lookups enforced before
# the dynamic condition evaluator. A matching (field, value) pair eliminates
# the listed disk types unconditionally — no condition evaluation logic
# can bypass this phase.
_STATIC_CONSTRAINT_MAP: dict[tuple[str, str], frozenset[str]] = {
    ("multiWriter", "Yes"): frozenset({
        "Hyperdisk Extreme", "Hyperdisk Throughput", "Hyperdisk ML",
        "PD Balanced", "PD SSD",
    }),
    ("appType", "Veeam Repository"): frozenset({
        "Hyperdisk Balanced", "Hyperdisk Balanced HA",
        "Hyperdisk Throughput", "Hyperdisk Extreme", "Hyperdisk ML",
    }),
    ("diskUsage", "Boot Only"): frozenset({"Hyperdisk ML"}),
    ("diskUsage", "Boot + Data"): frozenset({"Hyperdisk ML"}),
    ("machineType", "Legacy / Standard"): frozenset({
        "Hyperdisk Balanced", "Hyperdisk Balanced HA",
        "Hyperdisk Throughput", "Hyperdisk Extreme", "Hyperdisk ML",
    }),
}

# Constraint IDs whose semantics are fully captured by _STATIC_CONSTRAINT_MAP.
# The dynamic evaluator skips these to prevent any possibility of dual-path
# disagreement fom entering the triggered list.
_STATIC_CONSTRAINT_IDS: frozenset[str] = frozenset({
    "C-01", "C-02", "C-03", "C-09",
})

_WORKLOAD_PREFIXES = {"DB", "EA", "WA", "CP", "FS", "AN", "AI", "BA", "OT"}

def _yn(v: Any) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if v is None:
        return ""
    return str(v)


def _tri(v: Any) -> str:
    """Normalise an optional boolean to an explicit "Yes"/"No" — never blank.

    F2 support. These fields are opt-in flags: the user ticks "HA required" or
    they don't. "Not ticked" and "ticked No" mean the same thing to every rule
    that reads them, so an unanswered flag resolves to "No" rather than to
    missing.

    This matters because the table uses eq/ne pairs as exhaustive if/else
    branches. DB-01 fires on haRequired == "Yes" and DB-02 on
    haRequired != "Yes"; between them they must always award SQL Server a
    baseline. If an unanswered flag were left blank, strict `ne` would fail
    DB-02, DB-01 would already have failed, and a production SQL Server would
    fall through to PD Balanced on preference points alone. Verified: that is
    exactly what happens without this function.

    Required enums (deployment, appType, machineType) are deliberately NOT
    normalised — for those, absent really does mean unknown, and no rule
    should score them. That asymmetry is the whole point of F2.
    """
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if _is_missing(v):
        return "No"
    s = str(v).strip()
    return "Yes" if s.lower() in ("yes", "true", "1") else "No"

# ── Condition evaluator ──────────────────────────────────────────────────────
#
# F2. Missing-value semantics.
#
# V5 treated an absent field as a value: `ne` fired because None != "Single
# Zone", and every numeric comparison coerced a non-number to 0, so `lt` fired
# on data that was never supplied. The result was that an incomplete form
# still accumulated points. Observed: with deployment_model=None and
# ha_required=True, MOD-01 awarded Hyperdisk Balanced HA +35 for satisfying
# "deployment is not Single Zone" — a fact not in evidence.
#
# The rule now is: an unknown value satisfies nothing. A missing field fails
# eq, ne, in, and every numeric comparison. Rules must earn their points from
# data the user actually gave.
#
# Note this is a semantic change for `ne` specifically, and it is the intended
# one: "we don't know" is not the same claim as "it differs".
#
# Numeric fields are NOT affected in the common case, because to_context()
# already maps unknown iops/throughput to a real 0 meaning "no stated
# requirement". Rules like DB-05 ("Oracle baseline when no explicit
# high-performance requirement is present") depend on that 0 and keep working.

_MISSING = (None, "")


def _is_missing(v: Any) -> bool:
    """A field the user never supplied. Note 0 and False are NOT missing."""
    return v is None or (isinstance(v, str) and v.strip() == "")


def _numeric(v: Any) -> Optional[float]:
    """Coerce to a number, or None if this is not a usable numeric value.

    bool is excluded on purpose: in Python bool is a subclass of int, and
    silently reading True as 1 in an IOPS comparison would be a bug.
    """
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _eval_condition(cond: Any, ctx: dict, scores: Optional[dict] = None, *, _fail_closed: bool = False) -> bool:
    if not isinstance(cond, dict):
        return False
    typ = cond.get("type")

    if typ in ("and", "or"):
        subs = cond.get("conditions") or []
        if not subs:
            return False
        if typ == "and":
            return all(_eval_condition(c, ctx, scores, _fail_closed=_fail_closed) for c in subs)
        return any(_eval_condition(c, ctx, scores, _fail_closed=_fail_closed) for c in subs)

    if typ == "score_gt":
        if scores is None:
            return False
        actual = 0
        for dt, data in scores.items():
            if dt.lower() == str(cond.get("disk", "")).lower():
                actual = data.get("raw", 0)
                break
        return actual > cond["value"]

    if "field" not in cond:
        return False
    v = ctx.get(cond["field"])

    if typ == "eq":
        return (not _is_missing(v)) and v == cond["value"]
    if typ == "ne":
        if _fail_closed:
            return _is_missing(v) or v != cond["value"]
        return (not _is_missing(v)) and v != cond["value"]
    if typ == "in":
        return (not _is_missing(v)) and v in cond["value"]
    if typ == "missing":
        return _is_missing(ctx.get(cond["field"]))

    if typ in ("gte", "lte", "gt", "lt"):
        n = _numeric(v)
        t = _numeric(cond.get("value"))
        if n is None or t is None:
            return False
        if typ == "gte":
            return n >= t
        if typ == "lte":
            return n <= t
        if typ == "gt":
            return n > t
        return n < t

    return False

# ── Input data model ─────────────────────────────────────────────────────────

@dataclass
class WorkloadInput:
    workload_category: str
    application_type: str
    deployment_model: str
    multi_writer: bool
    shared_read: Optional[bool] = None
    environment: str = "Prod"
    dr_strategy: str = "None"
    machine_type_tier: str = "General Purpose"
    disk_usage: str = "Data Only"
    capacity_gib: int = 100
    iops: Optional[int] = None
    throughput_mibs: Optional[int] = None
    preference: str = "Balanced"
    read_write_pattern: Optional[str] = None
    ha_required: Optional[bool] = None
    ha_required_single_zone: Optional[bool] = None
    dr_required: Optional[bool] = None
    performance_known: Optional[bool] = None
    deployment_state: str = "New Deployment"
    current_disk_type: Optional[str] = None

    def to_context(self) -> dict:
        perf_known = self.performance_known
        if perf_known is None:
            perf_known = self.iops is not None or self.throughput_mibs is not None
        dr_req = self.dr_required
        if dr_req is None:
            dr_req = self.dr_strategy not in ("", "None", None)
        return {
            # Required enums: passed through as-is. A blank here is a genuine
            # unknown and every rule that reads it will decline to fire (F2).
            "workload": self.workload_category,
            "appType": self.application_type,
            "deployment": self.deployment_model,
            # Opt-in flags: always resolved to "Yes"/"No", never blank (F2).
            "multiWriter": _tri(self.multi_writer),
            "sharedRead": _tri(self.shared_read),
            "environment": self.environment,
            "drStrategy": self.dr_strategy,
            "machineType": self.machine_type_tier,
            "diskUsage": self.disk_usage,
            "capacity": self.capacity_gib or 0,
            "iops": self.iops if self.iops is not None else 0,
            "throughput": self.throughput_mibs if self.throughput_mibs is not None else 0,
            "preference": self.preference,
            "rwPattern": self.read_write_pattern or "Mixed",
            "haRequired": _tri(self.ha_required),
            "haRequiredSingleZone": _tri(self.ha_required_single_zone),
            "drRequired": _tri(dr_req),
            "performanceKnown": _tri(perf_known),
            "deploymentState": self.deployment_state,
            "currentDiskType": self.current_disk_type or "",
        }

# ── Constraints ───────────────────────────────────────────────────────────────

CONSTRAINT_MATRIX = [
    {
        "id": "C-01",
        "condition": {"type": "eq", "field": "multiWriter", "value": "Yes"},
        "eliminates": ["Hyperdisk Extreme", "Hyperdisk Throughput", "Hyperdisk ML", "PD Balanced", "PD SSD"],
        "classification": "VERIFIED",
        "source": "Selector scope: multi-writer candidates are limited to HA / clustered capable tiers.",
    },
    {
        "id": "C-02",
        "condition": {"type": "eq", "field": "appType", "value": "Veeam Repository"},
        "eliminates": ["Hyperdisk Balanced", "Hyperdisk Balanced HA", "Hyperdisk Throughput", "Hyperdisk Extreme", "Hyperdisk ML"],
        "classification": "VERIFIED",
        "source": "Veeam compatibility guardrail in current engine design.",
    },
    {
        "id": "C-03",
        "condition": {"type": "in", "field": "diskUsage", "value": ["Boot Only", "Boot + Data"]},
        "eliminates": ["Hyperdisk ML"],
        "classification": "VERIFIED",
        "source": "Hyperdisk ML is treated as data/shared-read only in this selector.",
    },
    {
        "id": "C-04",
        "condition": {"type": "and", "conditions": [
            {"type": "ne", "field": "machineType", "value": "Accelerator Optimized (GPU/TPU)"},
            {"type": "ne", "field": "machineType", "value": "Not sure / not yet decided"},
        ]},
        "eliminates": ["Hyperdisk ML"],
        "classification": "VERIFIED",
        "source": "Hyperdisk ML is scoped to accelerator / ML style workloads in this selector.",
    },
    {
        "id": "C-05",
        "condition": {"type": "or", "conditions": [
            {"type": "ne", "field": "workload", "value": "AI/ML"},
            {"type": "ne", "field": "sharedRead", "value": "Yes"},
        ]},
        "eliminates": ["Hyperdisk ML"],
        "classification": "ARCHITECTURAL",
        "source": "Engine scope: Hyperdisk ML only when shared-read AI/ML is explicitly selected.",
    },
    {
        "id": "C-06",
        "condition": {"type": "and", "conditions": [
            {"type": "eq", "field": "deployment", "value": "Single Zone"},
            {"type": "eq", "field": "multiWriter", "value": "No"},
        ]},
        "eliminates": ["Hyperdisk Balanced HA"],
        "classification": "ARCHITECTURAL",
        "source": "Single-zone non multi-writer does not require regional HA disk.",
    },
    {
        "id": "C-09",
        "condition": {"type": "eq", "field": "machineType", "value": "Legacy / Standard"},
        "eliminates": ["Hyperdisk Balanced", "Hyperdisk Balanced HA", "Hyperdisk Throughput", "Hyperdisk Extreme", "Hyperdisk ML"],
        "classification": "ARCHITECTURAL",
        "source": "Legacy machine type guardrail: prefer PD unless specific Hyperdisk support is validated.",
    },
]

# ── Scored rules ──────────────────────────────────────────────────────────────

SCORED_RULES = [
    # Database base rules
    {"id": "DB-01", "condition": {"type": "and", "conditions": [
        {"type": "eq", "field": "appType", "value": "Microsoft SQL Server"},
        {"type": "eq", "field": "haRequired", "value": "Yes"},
    ]}, "diskType": "Hyperdisk Balanced HA", "points": 45, "classification": "VERIFIED", "strength": "Strong Fit", "source": "SQL Server HA / FCI style cross-zone pattern"},
    {"id": "DB-02", "condition": {"type": "and", "conditions": [
        {"type": "eq", "field": "appType", "value": "Microsoft SQL Server"},
        {"type": "ne", "field": "haRequired", "value": "Yes"},
    ]}, "diskType": "Hyperdisk Balanced", "points": 35, "classification": "VERIFIED", "strength": "Good Fit", "source": "SQL Server general-purpose database baseline"},
    {"id": "DB-03", "condition": {"type": "eq", "field": "appType", "value": "SAP HANA"}, "diskType": "Hyperdisk Extreme", "points": 45, "classification": "CERTIFIED", "strength": "Strong Fit", "source": "SAP HANA high-performance database tier"},
    {"id": "DB-04", "condition": {"type": "and", "conditions": [
        {"type": "eq", "field": "appType", "value": "SAP HANA"},
        {"type": "in", "field": "environment", "value": ["Dev", "Test"]},
    ]}, "diskType": "Hyperdisk Balanced", "points": 35, "classification": "CERTIFIED", "strength": "Good Fit", "source": "SAP HANA non-production balanced-cost option"},
    {"id": "DB-05", "condition": {"type": "and", "conditions": [
        {"type": "eq", "field": "appType", "value": "Oracle Database"},
        {"type": "lt", "field": "iops", "value": 50000},
        {"type": "lt", "field": "throughput", "value": 1800},
    ]}, "diskType": "Hyperdisk Balanced", "points": 30, "classification": "VERIFIED", "strength": "Good Fit", "source": "Oracle baseline when explicit high-performance requirement is not present"},
    {"id": "DB-09", "condition": {"type": "and", "conditions": [
        {"type": "eq", "field": "appType", "value": "Oracle Database"},
        {"type": "gte", "field": "iops", "value": 50000},
    ]}, "diskType": "Hyperdisk Extreme", "points": 45, "classification": "ARCHITECTURAL", "strength": "Strong Fit", "source": "High-IOPS Oracle workload escalates to Extreme"},
    {"id": "DB-10", "condition": {"type": "and", "conditions": [
        {"type": "eq", "field": "appType", "value": "Oracle Database"},
        {"type": "gte", "field": "throughput", "value": 1800},
    ]}, "diskType": "Hyperdisk Extreme", "points": 30, "classification": "ARCHITECTURAL", "strength": "Strong Fit", "source": "High-throughput Oracle workload escalates to Extreme"},
    {"id": "DB-11", "condition": {"type": "and", "conditions": [
        {"type": "eq", "field": "appType", "value": "Oracle Database"},
        {"type": "eq", "field": "preference", "value": "Performance First"},
        {"type": "eq", "field": "environment", "value": "Prod"},
    ]}, "diskType": "Hyperdisk Extreme", "points": 20, "classification": "ARCHITECTURAL", "strength": "Performance Bias", "source": "Oracle production with performance-first preference"},
    {"id": "DB-12", "condition": {"type": "in", "field": "appType", "value": ["PostgreSQL", "MySQL", "MariaDB", "MongoDB", "Cassandra"]}, "diskType": "Hyperdisk Balanced", "points": 18, "classification": "ARCHITECTURAL", "strength": "Good Fit", "source": "General database baseline"},
    {"id": "DB-13", "condition": {"type": "and", "conditions": [
        {"type": "in", "field": "appType", "value": ["PostgreSQL", "MySQL", "MariaDB"]},
        {"type": "in", "field": "environment", "value": ["Dev", "Test"]},
        {"type": "eq", "field": "preference", "value": "Cost First"},
    ]}, "diskType": "PD Balanced", "points": 28, "classification": "ARCHITECTURAL", "strength": "Cost Fit", "source": "Small non-prod database cost optimization"},
    {"id": "DB-14", "condition": {"type": "in", "field": "appType", "value": ["IBM Db2", "Redis", "Elasticsearch / OpenSearch", "Other Database"]}, "diskType": "Hyperdisk Balanced", "points": 8, "classification": "FALLBACK", "strength": "Moderate Fit", "source": "Generic database fallback"},

    # Enterprise apps
    {"id": "EA-01", "condition": {"type": "eq", "field": "appType", "value": "Oracle E-Business Suite"}, "diskType": "Hyperdisk Balanced", "points": 30, "classification": "VERIFIED", "strength": "Good Fit", "source": "Oracle EBS general-purpose baseline"},
    {"id": "EA-02", "condition": {"type": "in", "field": "appType", "value": ["SAP S/4HANA", "SAP BW"]}, "diskType": "Hyperdisk Extreme", "points": 35, "classification": "CERTIFIED", "strength": "Good Fit", "source": "SAP database tier performance fit"},
    {"id": "EA-03", "condition": {"type": "in", "field": "appType", "value": ["SAP ECC", "SAP S/4HANA", "SAP BW"]}, "diskType": "Hyperdisk Balanced", "points": 25, "classification": "VERIFIED", "strength": "Good Fit", "source": "SAP application tier balanced fit"},
    {"id": "EA-04", "condition": {"type": "eq", "field": "workload", "value": "Enterprise Application"}, "diskType": "Hyperdisk Balanced", "points": 10, "classification": "FALLBACK", "strength": "Moderate Fit", "source": "Enterprise application fallback"},

    # Web/container
    {"id": "WA-01", "condition": {"type": "eq", "field": "workload", "value": "Web Application"}, "diskType": "PD Balanced", "points": 15, "classification": "ARCHITECTURAL", "strength": "Cost Fit", "source": "Web application general-purpose baseline"},
    {"id": "WA-02", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "workload", "value": "Web Application"}, {"type": "eq", "field": "preference", "value": "Performance First"}]}, "diskType": "Hyperdisk Balanced", "points": 18, "classification": "ARCHITECTURAL", "strength": "Good Fit", "source": "Performance-first web/application baseline"},
    {"id": "CP-01", "condition": {"type": "eq", "field": "workload", "value": "Container Platform"}, "diskType": "Hyperdisk Balanced", "points": 25, "classification": "VERIFIED", "strength": "Good Fit", "source": "Container platform baseline"},
    {"id": "CP-02", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "workload", "value": "Container Platform"}, {"type": "eq", "field": "haRequired", "value": "Yes"}]}, "diskType": "Hyperdisk Balanced HA", "points": 25, "classification": "VERIFIED", "strength": "Good Fit", "source": "Container platform HA baseline"},

    # File/analytics/AI/backup
    {"id": "FS-01", "condition": {"type": "eq", "field": "appType", "value": "Media Repository"}, "diskType": "Hyperdisk Throughput", "points": 35, "classification": "ARCHITECTURAL", "strength": "Good Fit", "source": "Sequential media repository throughput fit"},
    {"id": "FS-02", "condition": {"type": "eq", "field": "workload", "value": "File Services"}, "diskType": "Hyperdisk Balanced", "points": 8, "classification": "FALLBACK", "strength": "Moderate Fit", "source": "Block-backed file service fallback"},
    {"id": "AN-01", "condition": {"type": "in", "field": "appType", "value": ["Hadoop Platform", "Spark Platform", "Data Lake", "ETL Platform"]}, "diskType": "Hyperdisk Throughput", "points": 42, "classification": "VERIFIED", "strength": "Strong Fit", "source": "Throughput-heavy analytics fit"},
    {"id": "AN-02", "condition": {"type": "eq", "field": "workload", "value": "Analytics"}, "diskType": "Hyperdisk Balanced", "points": 12, "classification": "FALLBACK", "strength": "Moderate Fit", "source": "Analytics fallback"},
    {"id": "AI-01", "condition": {"type": "and", "conditions": [
        {"type": "eq", "field": "workload", "value": "AI/ML"},
        {"type": "eq", "field": "machineType", "value": "Accelerator Optimized (GPU/TPU)"},
        {"type": "eq", "field": "sharedRead", "value": "Yes"},
        {"type": "eq", "field": "diskUsage", "value": "Data Only"},
    ]}, "diskType": "Hyperdisk ML", "points": 80, "classification": "VERIFIED", "strength": "Strong Fit", "source": "Shared-read AI/ML dataset/model access"},
    {"id": "AI-02", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "workload", "value": "AI/ML"}, {"type": "eq", "field": "sharedRead", "value": "No"}]}, "diskType": "Hyperdisk Balanced", "points": 20, "classification": "ARCHITECTURAL", "strength": "Good Fit", "source": "AI/ML general-purpose scratch/storage"},
    {"id": "BA-01", "condition": {"type": "in", "field": "appType", "value": ["Commvault Repository", "Backup Repository", "Recovery Staging Area"]}, "diskType": "Hyperdisk Throughput", "points": 22, "classification": "ARCHITECTURAL", "strength": "Good Fit", "source": "Backup/recovery throughput pattern"},
    {"id": "BA-02", "condition": {"type": "in", "field": "appType", "value": ["Long-Term Archive", "Compliance Archive", "Rubrik Repository", "Cohesity Repository", "Other Backup Platform", "Veeam Repository"]}, "diskType": "PD Balanced", "points": 20, "classification": "ARCHITECTURAL", "strength": "Cost Fit", "source": "Backup/archive cost fit"},
    {"id": "OT-01", "condition": {"type": "eq", "field": "workload", "value": "Other"}, "diskType": "PD Balanced", "points": 10, "classification": "FALLBACK", "strength": "Moderate Fit", "source": "Generic fallback"},

    # HA / DR modifiers
    {"id": "MOD-01", "condition": {"type": "and", "conditions": [{"type": "ne", "field": "deployment", "value": "Single Zone"}, {"type": "eq", "field": "haRequired", "value": "Yes"}]}, "diskType": "Hyperdisk Balanced HA", "points": 35, "classification": "ARCHITECTURAL", "strength": "HA Fit", "source": "Synchronous cross-zone HA requirement"},
    {"id": "MOD-02", "condition": {"type": "and", "conditions": [{"type": "ne", "field": "deployment", "value": "Single Zone"}, {"type": "eq", "field": "haRequired", "value": "Yes"}, {"type": "gte", "field": "iops", "value": 50000}]}, "diskType": "Hyperdisk Extreme", "points": 10, "classification": "ARCHITECTURAL", "strength": "HA App-Layer Fit", "source": "High-performance HA may be handled with application replication"},
    {"id": "MOD-03", "condition": {"type": "eq", "field": "drStrategy", "value": "Active DR"}, "diskType": "Hyperdisk Balanced HA", "points": 20, "classification": "ARCHITECTURAL", "strength": "DR Fit", "source": "Active DR favors HA disk where performance fits"},
    {"id": "MOD-04", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "drStrategy", "value": "Active DR"}, {"type": "gte", "field": "iops", "value": 50000}]}, "diskType": "Hyperdisk Extreme", "points": 10, "classification": "ARCHITECTURAL", "strength": "DR Performance Fit", "source": "Active DR plus high performance can favor Extreme with app-level replication"},
    {"id": "MOD-05", "condition": {"type": "eq", "field": "drStrategy", "value": "Backup Only"}, "diskType": "PD Balanced", "points": 8, "classification": "ARCHITECTURAL", "strength": "Cost Fit", "source": "Backup-only DR is compatible with simpler disk tiers"},
    {"id": "MOD-06", "condition": {"type": "eq", "field": "drStrategy", "value": "Backup Only"}, "diskType": "Hyperdisk Balanced", "points": 8, "classification": "ARCHITECTURAL", "strength": "Good Fit", "source": "Snapshot-based backup-only DR"},

    # Environment / machine modifiers
    {"id": "MOD-07", "condition": {"type": "in", "field": "environment", "value": ["Dev", "Test"]}, "diskType": "PD Balanced", "points": 18, "classification": "ARCHITECTURAL", "strength": "Cost Fit", "source": "Non-production cost optimization"},
    {"id": "MOD-08", "condition": {"type": "eq", "field": "environment", "value": "Prod"}, "diskType": "Hyperdisk Balanced", "points": 8, "classification": "ARCHITECTURAL", "strength": "Prod Baseline", "source": "Production resiliency/performance baseline"},
    {"id": "MOD-09", "condition": {"type": "eq", "field": "environment", "value": "Prod"}, "diskType": "Hyperdisk Balanced HA", "points": 8, "classification": "ARCHITECTURAL", "strength": "Prod HA Baseline", "source": "Production HA baseline"},
    {"id": "MOD-10", "condition": {"type": "eq", "field": "machineType", "value": "Legacy / Standard"}, "diskType": "PD Balanced", "points": 25, "classification": "ARCHITECTURAL", "strength": "Compatibility Fit", "source": "Legacy machine compatibility"},
    {"id": "MOD-11", "condition": {"type": "in", "field": "machineType", "value": ["Compute Optimized", "Memory Optimized"]}, "diskType": "Hyperdisk Extreme", "points": 12, "classification": "ARCHITECTURAL", "strength": "Performance Fit", "source": "IOPS-heavy compute/memory optimized fit"},

    # Performance rules
    {"id": "PERF-01", "condition": {"type": "and", "conditions": [{"type": "gte", "field": "iops", "value": 5000}, {"type": "lt", "field": "iops", "value": 50000}]}, "diskType": "Hyperdisk Balanced", "points": 15, "classification": "ARCHITECTURAL", "strength": "Good Fit", "source": "Moderate performance requirement"},
    {"id": "PERF-02", "condition": {"type": "gte", "field": "iops", "value": 50000}, "diskType": "Hyperdisk Extreme", "points": 35, "classification": "ARCHITECTURAL", "strength": "Strong Fit", "source": "High IOPS requirement"},
    {"id": "PERF-03", "condition": {"type": "gte", "field": "iops", "value": 100000}, "diskType": "Hyperdisk Extreme", "points": 25, "classification": "VERIFIED", "strength": "Very Strong Fit", "source": "Very high IOPS requirement"},
    {"id": "PERF-04", "condition": {"type": "and", "conditions": [{"type": "gte", "field": "throughput", "value": 800}, {"type": "eq", "field": "rwPattern", "value": "Read Heavy"}]}, "diskType": "Hyperdisk Throughput", "points": 25, "classification": "ARCHITECTURAL", "strength": "Throughput Fit", "source": "Read-heavy sequential/throughput signal"},
    {"id": "PERF-05", "condition": {"type": "gte", "field": "throughput", "value": 1800}, "diskType": "Hyperdisk Extreme", "points": 25, "classification": "ARCHITECTURAL", "strength": "Strong Fit", "source": "High throughput plus low-latency/performance tier"},
    {"id": "PERF-06", "condition": {"type": "gte", "field": "capacity", "value": 1000}, "diskType": "Hyperdisk Balanced", "points": 8, "classification": "ARCHITECTURAL", "strength": "Scale Signal", "source": "Capacity scale signal"},
    {"id": "PERF-07", "condition": {"type": "and", "conditions": [{"type": "gte", "field": "throughput", "value": 1500}, {"type": "eq", "field": "workload", "value": "Analytics"}]}, "diskType": "Hyperdisk Throughput", "points": 35, "classification": "ARCHITECTURAL", "strength": "Strong Fit", "source": "Analytics throughput-heavy signal"},

    # Existing migration modifiers
    {"id": "MIG-01", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "deploymentState", "value": "Existing Migration"}, {"type": "eq", "field": "currentDiskType", "value": "SSD PD (pd-ssd)"}]}, "diskType": "PD SSD", "points": 8, "classification": "ARCHITECTURAL", "strength": "Current State", "source": "Existing PD SSD baseline comparison"},
    {"id": "MIG-02", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "deploymentState", "value": "Existing Migration"}, {"type": "eq", "field": "currentDiskType", "value": "Extreme PD (pd-extreme)"}]}, "diskType": "PD Extreme", "points": 15, "classification": "ARCHITECTURAL", "strength": "Current State", "source": "Existing PD Extreme baseline comparison"},
    {"id": "MIG-03", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "deploymentState", "value": "Existing Migration"}, {"type": "gte", "field": "iops", "value": 50000}]}, "diskType": "Hyperdisk Extreme", "points": 12, "classification": "ARCHITECTURAL", "strength": "Modernization Fit", "source": "High-performance migration modernization"},

    # Preference rules - intentionally modest; should not overpower explicit perf.
    {"id": "PREF-01", "condition": {"type": "eq", "field": "preference", "value": "Cost First"}, "diskType": "PD Balanced", "points": 22, "classification": "ARCHITECTURAL", "strength": "Cost Fit", "source": "Cost-first preference"},
    {"id": "PREF-02", "condition": {"type": "eq", "field": "preference", "value": "Cost First"}, "diskType": "Hyperdisk Extreme", "points": -25, "classification": "ARCHITECTURAL", "strength": "Cost Penalty", "source": "Cost-first penalty for highest-performance tier"},
    {"id": "PREF-03", "condition": {"type": "eq", "field": "preference", "value": "Balanced"}, "diskType": "Hyperdisk Balanced", "points": 15, "classification": "ARCHITECTURAL", "strength": "Preference Fit", "source": "Balanced preference"},
    {"id": "PREF-04", "condition": {"type": "eq", "field": "preference", "value": "Performance First"}, "diskType": "Hyperdisk Extreme", "points": 24, "classification": "ARCHITECTURAL", "strength": "Performance Fit", "source": "Performance-first preference"},
    {"id": "PREF-05", "condition": {"type": "eq", "field": "preference", "value": "Performance First"}, "diskType": "Hyperdisk Balanced", "points": 8, "classification": "ARCHITECTURAL", "strength": "Performance Baseline", "source": "Performance-first still allows balanced tier"},
    {"id": "PREF-06", "condition": {"type": "eq", "field": "preference", "value": "Performance First"}, "diskType": "PD Balanced", "points": -20, "classification": "ARCHITECTURAL", "strength": "Performance Penalty", "source": "Performance-first penalty for lowest-performance tier"},
]

WARNING_RULES = [
    {"id": "W-01", "condition": {"type": "eq", "field": "multiWriter", "value": "Yes"}, "text": "Multi-writer was selected. Validate application clustering, filesystem support, and the exact disk access mode before implementation."},
    {"id": "W-02", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "appType", "value": "SAP HANA"}, {"type": "ne", "field": "machineType", "value": "Memory Optimized"}]}, "text": "SAP HANA was selected without a Memory Optimized machine tier. Validate the machine family and SAP certification before implementation."},
    {"id": "W-03", "condition": {"type": "and", "conditions": [{"type": "gte", "field": "capacity", "value": 20480}, {"type": "in", "field": "workload", "value": ["Database", "Enterprise Application", "Analytics"]}]}, "text": "Large capacity selected. Evaluate storage pools / operational pooling if your platform standards support them."},
    {"id": "W-04", "condition": {"type": "eq", "field": "performanceKnown", "value": "No"}, "text": "Performance requirements were not provided. Treat this as an initial recommendation and validate actual IOPS/throughput before implementation."},
    {"id": "W-05", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "deployment", "value": "Multi Zone"}, {"type": "eq", "field": "haRequired", "value": "No"}]}, "text": "Multi-zone deployment was selected but GCP cross-zone disk replication was not requested. Confirm that HA is handled by the application layer, database replication, or another platform pattern."},
    {"id": "W-06", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "haRequired", "value": "Yes"}, {"type": "gte", "field": "throughput", "value": 2401}]}, "text": "Cross-zone HA was requested, but the throughput target is above the selector's Hyperdisk Balanced HA single-volume limit. Validate whether application-level replication with a zonal high-performance disk is the intended design."},
    {"id": "W-07", "condition": {"type": "and", "conditions": [{"type": "eq", "field": "appType", "value": "Oracle Database"}, {"type": "gte", "field": "iops", "value": 50000}, {"type": "eq", "field": "haRequired", "value": "Yes"}]}, "text": "High-performance Oracle plus HA detected. If Hyperdisk Extreme wins, storage-level cross-zone HA is not the reason; validate Oracle Data Guard/RAC/cluster design separately."},
]

RULES = {"constraints": CONSTRAINT_MATRIX, "scoredRules": SCORED_RULES, "warnings": WARNING_RULES}

# ── Engine helpers ────────────────────────────────────────────────────────────

def apply_constraints(ctx: dict) -> tuple[set[str], list[dict]]:
    excluded: set[str] = set()
    triggered: list[dict] = []

    # Phase 1: Static constraint enforcement via dictionary lookup.
    # Pure dict reads bypass the condition evaluator entirely.
    for (field, value), disks in _STATIC_CONSTRAINT_MAP.items():
        v = ctx.get(field)
        if not _is_missing(v) and v == value:
            newly = [d for d in disks if d not in excluded]
            excluded.update(newly)
            if newly:
                triggered.append({
                    "id": f"static_{field.replace(' ','_')}_{value.replace(' ','_')}",
                    "condition": {"type": "eq", "field": field, "value": value},
                    "eliminated": newly,
                    "classification": "VERIFIED",
                    "source": f"Static constraint: {field} = {value}",
                })

    # Phase 2: Dynamic constraint evaluation for compound conditions.
    # Uses _fail_closed=True so `ne` eliminates when the field is
    # missing rather than letting candidates slip through.
    for c in CONSTRAINT_MATRIX:
        if c["id"] in _STATIC_CONSTRAINT_IDS:
            continue
        if _eval_condition(c["condition"], ctx, _fail_closed=True):
            eliminated = [d for d in c.get("eliminates", []) if d not in excluded]
            excluded.update(eliminated)
            if eliminated:
                triggered.append({**c, "eliminated": eliminated})

    return excluded, triggered

def score_candidates(ctx: dict, excluded: set[str]) -> dict[str, dict]:
    scores: dict[str, dict] = {
        dt: {"raw": 0, "rules": []}
        for dt in DISK_TYPES
        if dt not in excluded
    }
    for rule in SCORED_RULES:
        dt = rule["diskType"]
        if dt in excluded or dt not in scores:
            continue
        if _eval_condition(rule["condition"], ctx):
            scores[dt]["raw"] += int(rule["points"])
            scores[dt]["rules"].append(rule)
    return scores

def _capability_reason(dt: str, ctx: dict) -> Optional[str]:
    caps = DISK_CAPABILITIES.get(dt, {})
    capacity = ctx.get("capacity") or 0
    iops = ctx.get("iops") or 0
    throughput = ctx.get("throughput") or 0
    if capacity and caps.get("max_capacity") and capacity > caps["max_capacity"]:
        return f"Exceeds disk capability: Capacity ({capacity} > {caps['max_capacity']} GiB)"
    if iops and caps.get("max_iops") and iops > caps["max_iops"]:
        return f"Exceeds disk capability: IOPS ({iops} > {caps['max_iops']})"
    if throughput and caps.get("max_throughput") and throughput > caps["max_throughput"]:
        return f"Exceeds disk capability: Throughput ({throughput} > {caps['max_throughput']} MiB/s)"
    return None

def apply_penalty_exclusions(scores: dict[str, dict], ctx: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """Split scored candidates into ranked vs excluded.

    F1. Each exclusion now carries an "exclusion_kind":

      "capability" — the disk physically cannot meet a stated requirement.
                     This is a hard fact and is never reversible.
      "score"      — the disk is merely a poor fit (raw <= 0). This is a
                     preference judgement and IS reversible by the fallback.

    V5 collapsed both into one bucket, and the fallback then re-promoted
    whichever had the highest raw score — including capability failures. See
    recommend() for the consequence.
    """
    ranked: dict[str, dict] = {}
    penalty_excluded: dict[str, dict] = {}
    for dt, data in scores.items():
        cap_reason = _capability_reason(dt, ctx)
        if cap_reason:
            penalty_excluded[dt] = {**data, "penalty_reason": cap_reason,
                                    "exclusion_kind": "capability"}
        elif data.get("raw", 0) <= 0:
            penalty_excluded[dt] = {**data, "penalty_reason": f"raw score = {data.get('raw', 0)}",
                                    "exclusion_kind": "score"}
        else:
            ranked[dt] = data
    return ranked, penalty_excluded

def _classification_rank(rule: dict) -> int:
    """F3: V5 did `str(...).split()[0]`, which raises IndexError on a blank or
    whitespace-only classification. It never fired because every rule in the
    table happens to set one, but it made the table impossible to extend
    safely. Guard instead of index."""
    parts = str(rule.get("classification", "")).split()
    return CLASSIFICATION_ORDER.get(parts[0], 0) if parts else 0


def _tie_break_key(item: tuple[str, dict], canonical: list[str]) -> tuple:
    dt, data = item
    rules = data.get("rules", [])
    best_class = max((_classification_rank(r) for r in rules), default=0)
    order = canonical.index(dt) if dt in canonical else 999
    return (-data.get("raw", 0), -best_class, order)


def _confidence(sorted_items: list[tuple[str, dict]], ctx: dict) -> int:
    """Confidence = how clearly the winner separated from the runner-up.

    F4. V5 had two calibration faults:

      1. `margin` was an absolute point difference, but raw scores are
         unbounded sums over however many rules happened to match. An Oracle
         + high-IOPS + performance-first workload matches six stacking rules
         and scores 159 vs 16 — margin 143, pinned to the 95% ceiling. That
         number reflects rule density in the table, not analytical certainty.
      2. `if top >= 90: base += 5` rewarded a high absolute score directly,
         compounding the same error.

    Relative margin is the honest measure: winning 159-to-16 and winning
    30-to-3 are the same claim about separation. A single surviving candidate
    is capped rather than treated as maximally certain — one option left is
    usually the constraint matrix narrowing the field, not evidence.
    """
    if not sorted_items:
        return 0

    top = sorted_items[0][1].get("raw", 0)
    if top <= 0:
        return 50

    if len(sorted_items) == 1:
        # Nothing to compare against. Constraints eliminated the field; that is
        # not the same as a confident recommendation.
        base = 80.0
    else:
        second = max(0, sorted_items[1][1].get("raw", 0))
        rel_margin = (top - second) / float(top)   # 0.0 (tied) .. 1.0 (uncontested)
        base = 55.0 + (35.0 * rel_margin)          # 55..90

    # Unstated performance requirements mean the inputs themselves are weak
    # evidence, regardless of how cleanly the rules separated.
    if ctx.get("performanceKnown") != "Yes":
        base = min(base, 75.0)

    # A near-tie is a near-tie even if the absolute scores are large.
    if len(sorted_items) > 1:
        second = max(0, sorted_items[1][1].get("raw", 0))
        if (top - second) / float(top) < 0.15:
            base = min(base, 72.0)

    return max(50, min(95, int(round(base))))

def normalize_and_rank(scores: dict[str, dict], ctx: Optional[dict] = None, canonical: Optional[list[str]] = None) -> dict:
    if canonical is None:
        canonical = CANONICAL_ORDER
    if ctx is None:
        ctx = {}
    ranked, penalty_excluded = apply_penalty_exclusions(scores, ctx)
    sorted_items = sorted(ranked.items(), key=lambda item: _tie_break_key(item, canonical))
    confidence = _confidence(sorted_items, ctx)
    ordered: dict[str, dict] = {}
    top_raw = sorted_items[0][1]["raw"] if sorted_items else 0
    for dt, data in sorted_items:
        display = confidence if dt == sorted_items[0][0] else int(max(1, round((data["raw"] / top_raw) * confidence))) if top_raw > 0 else 0
        ordered[dt] = {**data, "display": display}
    return {"ranked": ordered, "penalty_excluded": penalty_excluded, "confidence": confidence}

def collect_warnings(ctx: dict, scores: Optional[dict] = None) -> list[dict]:
    result: list[dict] = []
    for w in WARNING_RULES:
        if _eval_condition(w["condition"], ctx, scores=scores):
            result.append({"id": w["id"], "text": w["text"]})
    return result

# ── Main recommendation pipeline ──────────────────────────────────────────────

def recommend(inputs: WorkloadInput) -> dict:
    ctx = inputs.to_context()
    excluded, constraints = apply_constraints(ctx)
    scores = score_candidates(ctx, excluded)
    normalized = normalize_and_rank(scores, ctx=ctx, canonical=CANONICAL_ORDER)
    ranked = normalized["ranked"]
    penalty_excluded = normalized["penalty_excluded"]
    warnings = collect_warnings(ctx, scores=scores)

    fallback = False
    infeasible = False

    if not ranked and penalty_excluded:
        # F1. The V5 line below sorted ALL penalty-excluded candidates by raw
        # score and promoted the best one:
        #
        #     candidates = sorted(penalty_excluded.items(), key=lambda item: -item[1]["raw"])
        #
        # That bucket contained two different kinds of exclusion. Disks
        # excluded for a low score are a judgement call and can reasonably be
        # reconsidered. Disks excluded for failing a capability check cannot —
        # the hardware limit does not care about the score.
        #
        # Because cost-first preference rules award PD Balanced +22, it
        # frequently had the highest raw score in that bucket. Reproduced on
        # V5: a Veeam repository asking for 500,000 IOPS returned
        #
        #     winner = "PD Balanced"   (DISK_CAPABILITIES max_iops = 80,000)
        #     fallback = True
        #
        # while correctly listing PD SSD and PD Extreme as "Exceeds disk
        # capability". The engine recommended a disk it had already proven
        # could not do the job, at 6x under the requirement, flagged only by a
        # generic "validate manually" warning.
        #
        # Only score-excluded candidates are eligible for fallback now.
        eligible = {dt: d for dt, d in penalty_excluded.items()
                    if d.get("exclusion_kind") != "capability"}

        if eligible:
            candidates = sorted(
                eligible.items(),
                key=lambda item: (-item[1].get("raw", 0),
                                  CANONICAL_ORDER.index(item[0]) if item[0] in CANONICAL_ORDER else 999),
            )
            dt, data = candidates[0]
            ranked = {dt: {**data, "display": 50}}
            penalty_excluded = {k: v for k, v in penalty_excluded.items() if k != dt}
            fallback = True
            warnings.append({
                "id": "W-FB",
                "text": "Fallback selected because no candidate scored above zero. This disk is "
                        "capable of the stated requirement but is not a strong fit for the "
                        "workload. Validate manually before use.",
            })
        else:
            # Every survivor failed a hard capability check. There is no answer
            # to give, and inventing one is worse than saying so.
            infeasible = True
            shortfalls = "; ".join(
                f"{dt}: {d['penalty_reason'].replace('Exceeds disk capability: ', '')}"
                for dt, d in sorted(penalty_excluded.items(),
                                    key=lambda i: CANONICAL_ORDER.index(i[0])
                                    if i[0] in CANONICAL_ORDER else 999)
            )
            warnings.append({
                "id": "W-INF",
                "text": "No single volume can meet this requirement. Every remaining candidate "
                        f"failed a capability check ({shortfalls}). Consider striping across "
                        "multiple volumes, splitting the workload, or relaxing the target. "
                        "Note that these limits are per-volume and are also bounded by the VM "
                        "machine series and vCPU count.",
            })

    winner = next(iter(ranked.keys()), None)
    winner_data = ranked.get(winner, {}) if winner else {}
    return {
        "inputs": inputs,
        "context": ctx,
        "winner": winner,
        "winner_display": winner_data.get("display", 0),
        "winner_raw": winner_data.get("raw", 0),
        "ranked": ranked,
        "constraints": constraints,
        "excluded": sorted(excluded, key=lambda d: CANONICAL_ORDER.index(d) if d in CANONICAL_ORDER else 999),
        "penalty_excluded": penalty_excluded,
        "warnings": warnings,
        "fallback": fallback,
        # New in V6. Additive only — every key above is unchanged, so
        # modified_app.py keeps working untouched. When infeasible is True,
        # winner is None and ranked is empty, which app.py's existing
        # `if not winner and not ranking:` branch already renders as
        # "No eligible disk types found" plus the W-INF warning text.
        "infeasible": infeasible,
    }

# ── Rule table health check (F5) ──────────────────────────────────────────────
#
# The rule tables are the actual product here: ~60 dicts of hand-maintained
# strings. A typo in a "diskType" or "field" silently produces a rule that can
# never fire, and nothing in V5 would tell you. This runs at import, costs
# microseconds, and turns a silent scoring hole into a visible one.
#
# It reports rather than raises: a stale rule should not take down the app.


def _condition_fields(cond: Any):
    if not isinstance(cond, dict):
        return
    if cond.get("type") in ("and", "or"):
        for sub in cond.get("conditions") or []:
            yield from _condition_fields(sub)
    elif "field" in cond:
        yield cond["field"]


def _validate_rules() -> dict:
    """Static checks over the rule tables. Returns a report; never raises."""
    problems: list[str] = []
    notes: list[str] = []

    valid_fields = set(
        WorkloadInput(
            workload_category="", application_type="", deployment_model="", multi_writer=False
        ).to_context()
    )

    all_rules = (
        [("constraint", c) for c in CONSTRAINT_MATRIX]
        + [("scored", r) for r in SCORED_RULES]
        + [("warning", w) for w in WARNING_RULES]
    )

    seen: dict[str, str] = {}
    for kind, rule in all_rules:
        rid = rule.get("id", "<no id>")
        if rid in seen:
            problems.append(f"duplicate rule id {rid} ({seen[rid]} and {kind})")
        seen[rid] = kind

        for f in _condition_fields(rule.get("condition")):
            if f not in valid_fields:
                problems.append(f"{rid}: condition references unknown field '{f}'")

        if kind == "scored" and rule.get("diskType") not in DISK_TYPES:
            problems.append(f"{rid}: unknown diskType '{rule.get('diskType')}'")
        if kind == "constraint":
            for d in rule.get("eliminates", []):
                if d not in DISK_TYPES:
                    problems.append(f"{rid}: eliminates unknown diskType '{d}'")

    for dt in DISK_TYPES:
        if dt not in CANONICAL_ORDER:
            problems.append(f"'{dt}' is missing from CANONICAL_ORDER (tie-breaks sort it last)")
        if dt not in DISK_CAPABILITIES:
            problems.append(f"'{dt}' has no entry in DISK_CAPABILITIES (capability gate skipped)")

    # Reachability. A disk whose only positive rules are worth less than the
    # generic fallbacks its rivals get can never win, which makes it dead
    # inventory rather than a recommendation.
    for dt in DISK_TYPES:
        positive = [r for r in SCORED_RULES if r["diskType"] == dt and r["points"] > 0]
        if not positive:
            problems.append(f"'{dt}' has no positive scored rule and can never be recommended")
        else:
            ceiling = sum(r["points"] for r in positive)
            if ceiling < 20:
                notes.append(
                    f"'{dt}' max attainable score is {ceiling} "
                    f"(rules: {', '.join(r['id'] for r in positive)}) — effectively unreachable"
                )

    return {"problems": problems, "notes": notes}


RULE_HEALTH = _validate_rules()

# ── Known modelling gaps (not auto-fixed — these need a product decision) ─────
#
# G1. PD SSD and PD Extreme are effectively dead options. PD SSD's only
#     positive rule is MIG-01 (+8) and PD Extreme's is MIG-02 (+15), both
#     gated on deploymentState == "Existing Migration". Any rival picks up
#     more than that from environment/preference rules alone (MOD-08 Prod +8,
#     PREF-03 Balanced +15). So for a new deployment neither can ever win,
#     despite sitting in DISK_TYPES and CANONICAL_ORDER and being offered to
#     the user as if they were live candidates. Either give them real fit
#     rules or drop them from the selector.
#
# G2. Hyperdisk ML is unreachable when machineType is "Not sure / not yet
#     decided". C-04 deliberately lets that case through the constraint gate,
#     but AI-01 — the only rule that scores ML at all — requires machineType
#     == "Accelerator Optimized (GPU/TPU)". So ML survives elimination, scores
#     0, and is penalty-excluded anyway. The C-04 exemption buys nothing. Add
#     an AI-01B for the undecided case or tighten C-04.
#
# G3. FIXED as a side effect of F2's tri-state normalisation, and worth
#     knowing about because it changes recommendations. AI/ML with deployment
#     "Multi Region" used to score no AI rule at all: app.py only sets
#     shared_read for non-Multi-Region deployments, leaving it None, which
#     _yn() rendered as "". AI-01 needs sharedRead == "Yes" and AI-02 needs
#     "No", so neither fired and the answer fell through to generic
#     environment/preference points. _tri() now resolves the unanswered flag
#     to "No", AI-02 fires, and those workloads get the Hyperdisk Balanced
#     +20 baseline they were always meant to have. If you would rather the
#     app asked the shared-read question for Multi Region instead of the
#     engine assuming "No", that is a UI change in _build_inputs().
#
# G4. DISK_CAPABILITIES lists max_capacity as 65536 GiB for all eight types
#     and treats limits as per-volume constants. Real limits vary by type and
#     are additionally bounded by machine series and vCPU count, as the
#     comment above the table already concedes. These numbers should be
#     verified against current GCP documentation before anyone trusts the
#     capability gate quantitatively; V6 fixes how the gate is *applied*, not
#     what it contains.
#
# G5. Non-winner "display" percentages are raw/top_raw * confidence. That is a
#     share-of-points ratio being rendered to the user as "%", next to a
#     winner figure that is a confidence measure. Two different quantities in
#     the same column.

if __name__ == "__main__":  # pragma: no cover
    for p in RULE_HEALTH["problems"]:
        print(f"PROBLEM: {p}")
    for n in RULE_HEALTH["notes"]:
        print(f"NOTE:    {n}")
    if not RULE_HEALTH["problems"]:
        print("Rule table: no structural problems found.")
