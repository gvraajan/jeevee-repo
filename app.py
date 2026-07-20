"""PD vs Hyperdisk Advisor — Modified: Split-Screen Layout with Persistent Review Sidebar."""

import streamlit as st

from engine import WorkloadInput, recommend, CANONICAL_ORDER

# ── Persistent cache keys (survive widget unmount) ──────────────────────

_CACHED_KEYS = [
    "workload", "app_type", "deploy_state", "current_disk",
    "deployment", "multi_writer", "shared_read", "ha_required",
    "ha_single_zone", "environment", "dr_strategy", "machine_type",
    "disk_usage", "capacity", "perf_known", "iops", "throughput",
    "rw_pattern", "preference",
]
if "form_cache" not in st.session_state:
    st.session_state.form_cache = {}

_PLACEHOLDER = "--- Select an Option ---"

_APP_TYPES: dict[str, list[str]] = {
    "Database": [
        "Microsoft SQL Server", "SAP HANA", "Oracle Database",
        "PostgreSQL", "MySQL", "MariaDB", "MongoDB", "Cassandra",
        "IBM Db2", "Redis", "Elasticsearch / OpenSearch",
        "Other Database",
    ],
    "Enterprise Application": [
        "Oracle E-Business Suite", "SAP S/4HANA", "SAP BW", "SAP ECC",
        "Oracle Fusion", "Salesforce Integration Platform",
        "ServiceNow Platform", "PeopleSoft", "Siebel",
        "Custom Enterprise Application", "ERP Application",
        "CRM Application", "Other Enterprise Application",
    ],
    "Web Application": [
        "Java Application", ".NET Application", "PHP Application",
        "E-commerce Platform", "CMS Platform", "Web API Platform",
        "Frontend Application", "Other Web Application",
    ],
    "Container Platform": [
        "GKE Platform", "Containerized Enterprise Application",
        "CI/CD Platform", "Microservices Platform",
        "Service Mesh Platform", "Serverless Platform",
        "Other Container Platform",
    ],
    "Analytics": [
        "Hadoop Platform", "Spark Platform", "Data Lake",
        "Data Warehouse", "Reporting Platform",
        "Business Intelligence Platform", "ETL Platform",
        "Other Analytics Platform", "BigQuery Analytics",
    ],
    "AI/ML": [
        "AI Inference Platform", "Generative AI Platform", "LLM Workload",
        "AI Training Cluster", "MLOps Platform", "GPU Compute Cluster",
        "Vector Database Platform", "Data Science Workbench",
        "Other AI/ML Platform",
    ],
    "Backup & Archive": [
        "Veeam Repository", "Commvault Repository", "Backup Repository",
        "Recovery Staging Area", "Long-Term Archive", "Compliance Archive",
        "Rubrik Repository", "Cohesity Repository",
        "Other Backup Platform",
    ],
    "Other": [
        "Custom Application", "Third Party Product", "Unknown", "Other",
        "Legacy Application",
    ],
}

_DEPLOYMENTS = [_PLACEHOLDER, "Single Zone", "Multi Zone", "Multi Region"]
_ENVIRONMENTS = [_PLACEHOLDER, "Dev", "Test", "UAT", "Prod"]
_DR_STRATEGIES = [_PLACEHOLDER, "None", "Backup Only", "Cross Region Recovery", "Active DR"]
_MACHINE_TYPES = [
    _PLACEHOLDER, "Legacy / Standard", "General Purpose", "Compute Optimized",
    "Memory Optimized", "Accelerator Optimized (GPU/TPU)",
    "Not sure / not yet decided",
]
_DISK_USAGES = [_PLACEHOLDER, "Boot Only", "Data Only", "Boot + Data"]
_PREFERENCES = [_PLACEHOLDER, "Cost First", "Balanced", "Performance First"]
_RW_PATTERNS = [_PLACEHOLDER, "Read Heavy", "Write Heavy", "Mixed"]
_DEPLOY_STATES = [_PLACEHOLDER, "New Deployment", "Existing Migration"]

st.set_page_config(
    page_title="PD vs Hyperdisk Advisor",
    page_icon=":cloud:",
    layout="wide",
    initial_sidebar_state="expanded",
)

_DOC_LINKS = {
    "hyperdisk": "https://cloud.google.com/compute/docs/disks/hyperdisks",
    "persistent_disk": "https://cloud.google.com/compute/docs/disks/persistent-disks",
    "choose_disk": "https://cloud.google.com/compute/docs/disks",
}

_SECTION_HELP = {
    1: ("Workload Profile",
        "Tell the engine what you're running and whether it's new or a migration. "
        "The workload category and application type drive which application-specific "
        "scoring rules apply later in the pipeline."),
    2: ("Deployment & Availability",
        "Describe how the workload is spread across zones/regions, and whether it "
        "needs multi-writer or cross-zone (HA) replication. These answers determine "
        "which disk types get hard-eliminated before scoring even starts."),
    3: ("Environment & DR",
        "Set the deployment tier (Dev/Test/UAT/Prod) and disaster recovery approach. "
        "Production and DR-aware workloads are scored more conservatively."),
    4: ("Infrastructure",
        "Specify the machine family, disk role (boot/data), and capacity. These are "
        "checked against each disk type's machine-series compatibility and maximum "
        "single-volume capacity."),
    5: ("Performance",
        "Provide target IOPS/throughput if you know them. The engine hard-checks "
        "candidates against each disk type's performance ceiling; if you don't know "
        "them yet, the recommendation is based on workload profile alone and flagged "
        "as unverified."),
    6: ("Preference",
        "Bias the final ranking toward lower cost, balance, or maximum performance "
        "when multiple disk types are otherwise close in score."),
}

# ── Dark Enterprise Console Theme + Idle-Lock Overlay ────────────────────

st.markdown("""
<style>
.stApp {
    background-color: #111111;
    color: #FFFFFF;
}
.main > div {
    background-color: #111111 !important;
}
.stApp header {
    background-color: #0a0a0a !important;
}
[data-testid="stAppViewContainer"] {
    background-color: #111111 !important;
}
[data-testid="stHeader"] {
    background-color: #0a0a0a !important;
}
[data-testid="stToolbar"] {
    background-color: #0a0a0a !important;
}
h1, h2, h3, h4, h5, h6, p, span, label, .stCaption, .stMarkdown {
    color: #FFFFFF !important;
}
.stSelectbox label, .stRadio label, .stCheckbox label, .stNumberInput label {
    color: #FFFFFF !important;
}
.stSelectbox div[data-baseweb="select"] > div {
    background-color: #1a1a1a !important;
    border-color: #333333 !important;
    color: #FFFFFF !important;
}
.stSelectbox div[data-baseweb="select"] > div:hover {
    border-color: #4FC3F7 !important;
}
div[data-baseweb="select"] {
    background-color: #1a1a1a !important;
}
div[data-baseweb="select"] span {
    color: #FFFFFF !important;
}
div[data-baseweb="popover"], div[data-baseweb="menu"] {
    background-color: #1a1a1a !important;
}
div[data-baseweb="menu"] li {
    color: #FFFFFF !important;
}
div[data-baseweb="menu"] li:hover {
    background-color: #333333 !important;
}
div[role="listbox"] ul {
    background-color: #1a1a1a !important;
}
div[role="listbox"] li {
    color: #FFFFFF !important;
}
div[role="listbox"] li:hover {
    background-color: #333333 !important;
}
.stRadio div[role="radiogroup"] label {
    color: #FFFFFF !important;
}
.stCheckbox label {
    color: #FFFFFF !important;
}
.stNumberInput input {
    background-color: #1a1a1a !important;
    border-color: #333333 !important;
    color: #FFFFFF !important;
}
.stNumberInput input:focus {
    border-color: #4FC3F7 !important;
}
.stTextInput input {
    background-color: #1a1a1a !important;
    border-color: #333333 !important;
    color: #FFFFFF !important;
}
.stTextInput input:focus {
    border-color: #4FC3F7 !important;
}
.stButton button {
    background-color: #00E676 !important;
    color: #111111 !important;
    font-weight: 700 !important;
    border: none !important;
    border-radius: 6px !important;
    transition: all 0.2s ease !important;
}
.stButton button:hover {
    background-color: #00C853 !important;
    box-shadow: 0 0 20px rgba(0, 230, 118, 0.4) !important;
}
.stButton button[kind="secondary"] {
    background-color: transparent !important;
    border: 1px solid #4FC3F7 !important;
    color: #4FC3F7 !important;
}
.stButton button[kind="secondary"]:hover {
    background-color: rgba(79, 195, 247, 0.1) !important;
}
.winner-card {
    background: linear-gradient(135deg, #00E676 0%, #00BCD4 100%) !important;
    color: #111111 !important;
    padding: 1.5rem;
    border-radius: 12px;
    margin: 1rem 0;
    border: 1px solid rgba(0, 230, 118, 0.3);
    box-shadow: 0 4px 24px rgba(0, 230, 118, 0.15);
}
.winner-card h2 {
    margin: 0 0 0.25rem 0;
    font-size: 1.8rem;
    color: #111111 !important;
}
.winner-card p {
    margin: 0;
    opacity: 0.9;
    color: #111111 !important;
}
.rank-entry {
    display: flex;
    justify-content: space-between;
    padding: 0.6rem 0.8rem;
    border-bottom: 1px solid #333333;
    font-size: 0.95rem;
    color: #FFFFFF !important;
}
.rank-entry:last-child { border-bottom: none; }
.rank-entry .score {
    font-weight: 600;
    min-width: 80px;
    text-align: right;
    color: #00E676 !important;
}
.rank-entry.winner-row {
    background: rgba(0, 230, 118, 0.08) !important;
    border-radius: 6px;
    font-weight: 700;
    border-left: 3px solid #00E676;
}
.constraint-badge {
    display: inline-block;
    background: rgba(244, 67, 54, 0.15) !important;
    color: #EF5350 !important;
    padding: 0.2rem 0.6rem;
    border-radius: 4px;
    font-size: 0.8rem;
    margin: 0.15rem;
}
.warning-badge {
    display: inline-block;
    background: rgba(255, 193, 7, 0.15) !important;
    color: #FFD54F !important;
    padding: 0.2rem 0.6rem;
    border-radius: 4px;
    font-size: 0.8rem;
    margin: 0.15rem;
}
.section-header {
    font-size: 1.1rem;
    font-weight: 700;
    margin: 1.5rem 0 0.75rem 0;
    padding-bottom: 0.25rem;
    border-bottom: 2px solid #4FC3F7;
    color: #4FC3F7 !important;
}
.summary-box {
    background: #1a1a1a;
    border: 1px solid #333333;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin: 0.5rem 0;
    font-size: 0.9rem;
}
.summary-box .summary-label {
    color: #888888 !important;
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}
.summary-box .summary-value {
    color: #FFFFFF !important;
    font-weight: 600;
}
.stProgress > div > div {
    background-color: #00E676 !important;
}
.stInfo {
    background-color: rgba(79, 195, 247, 0.1) !important;
    border: 1px solid #4FC3F7 !important;
    color: #FFFFFF !important;
}
.stError {
    background-color: rgba(244, 67, 54, 0.15) !important;
    border: 1px solid #EF5350 !important;
    color: #FFFFFF !important;
}
.stWarning {
    background-color: rgba(255, 193, 7, 0.1) !important;
    border: 1px solid #FFD54F !important;
    color: #FFFFFF !important;
}
.stSuccess {
    background-color: rgba(0, 230, 118, 0.1) !important;
    border: 1px solid #00E676 !important;
    color: #FFFFFF !important;
}
.stExpander {
    background-color: #1a1a1a !important;
    border: 1px solid #333333 !important;
    border-radius: 8px !important;
}
.stExpander summary {
    color: #FFFFFF !important;
}
.st-emotion-cache-1qg05tj, .st-emotion-cache-10trblm, .st-emotion-cache-1wbqy5l {
    color: #FFFFFF !important;
}
[data-testid="stSidebar"] {
    background-color: #111111 !important;
    border-right: 1px solid #2a2a2a !important;
}
[data-testid="stSidebar"] .stMarkdown {
    color: #FFFFFF !important;
}
</style>

<script>
(function() {
    var IDLE_TIMEOUT = 5 * 60 * 1000;  # For testing 5 minutes
    var idleTimer = null;
    var overlayShown = false;

    function showIdleOverlay() {
        if (overlayShown) return;
        overlayShown = true;
        var overlay = document.createElement('div');
        overlay.id = 'idle-overlay';
        overlay.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;background:rgba(0,0,0,0.93);z-index:999999;display:flex;align-items:center;justify-content:center;';
        overlay.innerHTML = '<div style="text-align:center;max-width:520px;padding:2rem;"><div style="font-size:3.5rem;margin-bottom:1rem;">&#9203;</div><h1 style="color:#ffffff;margin:0 0 0.5rem 0;font-size:1.6rem;font-weight:700;">Session Idle</h1><p style="color:#b0bec5;margin:0 0 1.5rem 0;font-size:0.95rem;line-height:1.6;">Your session has been idle for 30 minutes. To conserve Google Cloud container compute resources, this session has been locked.</p><p style="color:#666;font-size:0.8rem;">Please refresh the page to continue your work.</p></div>';
        document.body.appendChild(overlay);
    }

    function resetIdleTimer() {
        if (overlayShown) return;
        if (idleTimer) clearTimeout(idleTimer);
        idleTimer = setTimeout(showIdleOverlay, IDLE_TIMEOUT);
    }

    ['click','keydown','mousemove','scroll','touchstart'].forEach(function(evt) {
        document.addEventListener(evt, resetIdleTimer, {passive:true});
    });
    resetIdleTimer();
})();
</script>
""", unsafe_allow_html=True)

title_col, help_col = st.columns([6, 1])
with title_col:
    st.title(":cloud: PD vs Hyperdisk Advisor")
    st.caption("Enter your workload details to get a data-driven Hyperdisk recommendation.")
with help_col:
    st.write("")
    st.write("")
    with st.popover("❓ Help", use_container_width=True):
        st.markdown("#### What is this?")
        st.write(
            "This advisor recommends the best GCP block storage type — Persistent "
            "Disk (PD) or Hyperdisk — for your workload. It runs your inputs through "
            "a rules engine: hard constraints eliminate disk types that can't meet "
            "your requirements, then a scoring model ranks the survivors and reports "
            "a confidence level for the top pick."
        )
        st.markdown("#### Common questions")
        with st.expander("What's the difference between PD and Hyperdisk?"):
            st.write(
                "Persistent Disk (PD) is Google's original block storage family "
                "(Balanced, SSD, Extreme) with performance tied to disk size. "
                "Hyperdisk is the newer generation that lets you provision IOPS "
                "and throughput independently of capacity, and includes variants "
                "tuned for HA, throughput-heavy, extreme-performance, and ML workloads."
            )
        with st.expander("How is the confidence score calculated?"):
            st.write(
                "Confidence reflects how clearly the top candidate beat the runner-up: "
                "a bigger scoring margin between #1 and #2 means higher confidence. "
                "It's capped lower when performance requirements (IOPS/throughput) "
                "weren't provided, since the recommendation is then based on fewer facts."
            )
        with st.expander("What if I don't know my IOPS or throughput?"):
            st.write(
                "Leave 'performance requirements known' set to No. The engine will "
                "still recommend a disk type using your workload profile, but the "
                "result is flagged as an initial recommendation to validate before "
                "implementation."
            )
        with st.expander("Why was a disk type excluded?"):
            st.write(
                "Disk types are excluded for two reasons: a hard constraint (e.g. a "
                "capability a disk can never meet for this workload) or a capability "
                "limit (your requested IOPS, throughput, or capacity exceeds what a "
                "single volume of that disk type supports)."
            )
        with st.expander("What do the warnings mean?"):
            st.write(
                "Warnings don't block a recommendation — they flag design decisions "
                "worth double-checking, such as validating multi-writer clustering "
                "support or confirming HA is handled at the application layer."
            )

st.info(
    "**What this tool is for:** a quick, defensible starting point for choosing between "
    "Google Cloud Persistent Disk and Hyperdisk families for a given workload -- sanity-checking "
    "a design, comparing how HA/performance/capacity requirements shift the right disk type, or "
    "prepping for a storage review. It's a decision aid, not a substitute for validating the "
    "final choice against your target machine type, region, and quota.\n\n"
    f"**Reference docs:** [About Hyperdisk]({_DOC_LINKS['hyperdisk']}) | "
    f"[About Persistent Disk]({_DOC_LINKS['persistent_disk']}) | "
    f"[Choose a disk type]({_DOC_LINKS['choose_disk']})\n\n"
    "*Built from Google Cloud documentation available as of July 2026. Disk types, limits, "
    "and pricing change over time -- always confirm current specs in the official docs above "
    "before finalizing a design.*"
)


def section_header(step: int) -> None:
    title, brief = _SECTION_HELP[step]
    col1, col2 = st.columns([10, 1])
    with col1:
        st.markdown(f'<div class="section-header">{step} &nbsp; {title}</div>', unsafe_allow_html=True)
    with col2:
        with st.popover("\u2753"):
            st.write(brief)


# ── Session state initialisation ──────────────────────────────────────────

_defaults = {
    "step": 1,
    "workload": None,
    "app_type": None,
    "deploy_state": _PLACEHOLDER,
    "current_disk": None,
    "deployment": None,
    "multi_writer": False,
    "shared_read": None,
    "ha_required": None,
    "ha_single_zone": None,
    "environment": _PLACEHOLDER,
    "dr_strategy": _PLACEHOLDER,
    "machine_type": _PLACEHOLDER,
    "disk_usage": _PLACEHOLDER,
    "capacity": 100,
    "perf_known": _PLACEHOLDER,
    "iops": None,
    "throughput": None,
    "rw_pattern": _PLACEHOLDER,
    "preference": _PLACEHOLDER,
}
for k, v in _defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# Seed form_cache from initial widget values (never overwrite committed data)
for _key in _CACHED_KEYS:
    if _key not in st.session_state.form_cache:
        st.session_state.form_cache[_key] = st.session_state.get(_key)


# ── Persistent cache sync helpers ─────────────────────────────────────────

def _fc_clear(*names: str) -> None:
    """Reset fields to their declared default in BOTH form_cache and widget state.

    Needed because _fc_sync deliberately refuses to write None/_PLACEHOLDER —
    which means a value, once set, can never be removed through the normal
    path. That is fine for fields the user can always see and change. It is a
    bug for conditional fields, which the UI hides without clearing.
    """
    for name in names:
        default = _defaults.get(name)
        st.session_state.form_cache[name] = default
        # Assigning a widget key inside an on_change callback is supported:
        # the callback runs before widgets are re-instantiated next script run.
        if name in st.session_state:
            st.session_state[name] = default


def _fc_sync(name: str, clears: tuple[str, ...] = ()):
    """Persist a widget value, and reset any fields that depend on it.

    BUGFIX — stale conditional state.

    The Multi-Writer checkbox only renders for Single Zone, or for Multi Zone
    when HA == "Yes" (line ~863). Hiding a widget does not clear it, nothing
    here ever reset it, and _maybe_restore_cache actively restores it when the
    widget reappears. So:

        1. Multi Zone, HA = Yes  -> user ticks Multi-Writer
        2. User flips HA to No   -> checkbox disappears
        3. form_cache["multi_writer"] is STILL True
        4. _build_inputs passes multi_writer=True to the engine
        5. C-01 eliminates five of eight disk types

    Measured on the SAP S/4HANA case from the screenshot:

        UI shows (False) -> Hyperdisk Extreme   65%, 1 disk eliminated
        Engine gets (True) -> Hyperdisk Balanced 86%, 5 disks eliminated

    Wrong answer, HIGHER confidence (a narrower field looks like a cleaner
    win), and no visible cause. `clears` names the children to reset whenever
    this field changes.
    """
    def _inner():
        val = st.session_state.get(name)
        if val is not None and val != _PLACEHOLDER:
            st.session_state.form_cache[name] = val
        if clears:
            _fc_clear(*clears)
    return _inner


def _maybe_restore_cache(name: str) -> None:
    cached = st.session_state.form_cache.get(name)
    if cached is not None and (name not in st.session_state or st.session_state.get(name) is None):
        st.session_state[name] = cached


def _sync_all_cached() -> None:
    for key in _CACHED_KEYS:
        if key in st.session_state:
            val = st.session_state[key]
            if val is not None and val != _PLACEHOLDER:
                st.session_state.form_cache[key] = val


def _clear_result() -> None:
    if "result" in st.session_state:
        del st.session_state.result


# ── Sidebar: Cost Banner + Live Tracking Panel ───────────────────────────

def _sidebar_value(key: str) -> str | None:
    v = st.session_state.form_cache.get(key)
    if v is None or v == "" or v == _PLACEHOLDER:
        return None
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if key == "capacity" and isinstance(v, (int, float)):
        return f"{int(v):,} GiB"
    return str(v)


def _render_sidebar() -> None:
    _sync_all_cached()
    with st.sidebar:
        st.markdown(
            """
            <div style="background:linear-gradient(135deg,#0d2137 0%,#1a3a5c 100%);
                        border:1px solid #2979ff;border-radius:10px;padding:0.85rem 1rem;
                        margin-bottom:1.5rem;">
                <div style="font-size:0.72rem;font-weight:800;color:#64b5f6;
                            text-transform:uppercase;letter-spacing:0.1em;
                            margin-bottom:0.5rem;">
                    \u23f1\ufe0f WEB APP SESSION TIMEOUT ACTIVE
                </div>
                <div style="font-size:0.78rem;color:#b0bec5;line-height:1.6;">
                    To prevent cloud container resource leakage, this specific
                    browser tab will pause and prompt for a refresh after 30
                    minutes of inactivity.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            """
            <div style="font-size:0.85rem;font-weight:700;color:#4FC3F7;
                        margin-bottom:0.75rem;padding-bottom:0.3rem;
                        border-bottom:2px solid #4FC3F7;">
                &#x1f4cb; Review Choices Made So Far
            </div>
            """,
            unsafe_allow_html=True,
        )

        fields = [
            ("Category", "workload"),
            ("App Type", "app_type"),
            ("Deploy State", "deploy_state"),
            ("Deployment", "deployment"),
            # BUGFIX — these four move the recommendation but were absent from
            # a panel titled "Review Choices Made So Far". On the SAP S/4HANA
            # screenshot, HA=Yes fired MOD-01 (+35 to Hyperdisk Balanced HA)
            # and MOD-02 (+10 to Hyperdisk Extreme), taking the winner from
            # raw 94 to 104 — with no HA row anywhere in this list. An input
            # that changes the answer must never be invisible here.
            ("HA Required", "ha_required"),
            ("HA (Single Zone)", "ha_single_zone"),
            ("Multi-Writer", "multi_writer"),
            ("Shared Read", "shared_read"),
            ("Environment", "environment"),
            ("DR Strategy", "dr_strategy"),
            ("Machine Type", "machine_type"),
            ("Disk Usage", "disk_usage"),
            ("Capacity", "capacity"),
            ("Performance", "perf_known"),
            ("Preference", "preference"),
        ]

        # Conditional fields: which deployment models actually ask for them.
        # A field is rendered when it is relevant OR when it holds a value.
        # The second half matters: it is what makes stale state visible instead
        # of silent. Never hide a field just because it is not applicable —
        # if it is set and not applicable, that IS the thing worth seeing.
        _conditional = {
            "ha_required": lambda d: d == "Multi Zone",
            "ha_single_zone": lambda d: d == "Single Zone",
            "multi_writer": lambda d: d in ("Single Zone", "Multi Zone"),
            "shared_read": lambda d: st.session_state.form_cache.get("workload") == "AI/ML"
                                     and d in ("Single Zone", "Multi Zone"),
        }
        _dep = st.session_state.form_cache.get("deployment")

        rows = ""
        for label, key in fields:
            val = _sidebar_value(key)
            if key in _conditional and not _conditional[key](_dep):
                # Not applicable. Skip it — unless it is set, in which case it
                # is stale state reaching the engine and must be shown.
                raw = st.session_state.form_cache.get(key)
                if raw in (None, False, "", _PLACEHOLDER):
                    continue
                label = f"{label} (n/a!)"
            if val is None:
                display = '<span style="color:#555;">--- Awaiting Input ---</span>'
            else:
                display = f'<span style="color:#fff;font-weight:600;">{val}</span>'
            rows += (
                f'<div style="display:flex;justify-content:space-between;'
                f'align-items:center;padding:0.35rem 0;border-bottom:1px solid #2a2a2a;'
                f'font-size:0.78rem;">'
                f'<span style="color:#888;">{label}</span>'
                f'{display}'
                f'</div>'
            )

        st.markdown(
            f'<div style="background:#1a1a1a;border:1px solid #333;border-radius:8px;'
            f'padding:0.25rem 0.75rem;">{rows}</div>',
            unsafe_allow_html=True,
        )


_render_sidebar()


# ── Helper functions ──────────────────────────────────────────────────────

def _summary_rows(pairs: list[tuple[str, str | None]]) -> str:
    rows = ""
    for label, value in pairs:
        if value is not None and value != "":
            rows += (
                f'<span class="summary-label">{label}</span>'
                f'<span class="summary-value">{value}</span>'
            )
    if not rows:
        return ""
    return (
        f'<div class="summary-box">'
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;">'
        f'{rows}</div></div>'
    )


def _render_step_summary(step_num: int) -> None:
    pairs: list[tuple[str, str | None]] = []

    def _valid(v):
        return v is not None and v != "" and v != _PLACEHOLDER

    if step_num == 1:
        pairs = [
            ("Category", st.session_state.form_cache.get("workload")),
            ("Application", st.session_state.form_cache.get("app_type")),
            ("Deployment State", st.session_state.form_cache.get("deploy_state")),
        ]
        cur = st.session_state.form_cache.get("current_disk")
        if st.session_state.form_cache.get("deploy_state") == "Existing Migration" and _valid(cur):
            pairs.append(("Current Disk", cur))
    elif step_num == 2:
        dep = st.session_state.form_cache.get("deployment")
        pairs.append(("Deployment Model", dep))
        if dep == "Single Zone":
            pairs.append(("Multi-Writer", "Yes" if st.session_state.form_cache.get("multi_writer") else "No"))
        elif dep == "Multi Zone":
            ha = st.session_state.form_cache.get("ha_required")
            if _valid(ha):
                pairs.append(("HA Required", ha))
            if ha == "Yes":
                pairs.append(("Multi-Writer", "Yes" if st.session_state.form_cache.get("multi_writer") else "No"))
        sr = st.session_state.form_cache.get("shared_read")
        if st.session_state.form_cache.get("workload") == "AI/ML" and dep and dep != "Multi Region" and _valid(sr):
            pairs.append(("Shared Read", sr))
    elif step_num == 3:
        pairs = [
            ("Environment", st.session_state.form_cache.get("environment")),
            ("DR Strategy", st.session_state.form_cache.get("dr_strategy")),
        ]
    elif step_num == 4:
        cap = st.session_state.form_cache.get("capacity")
        pairs = [
            ("Machine Type", st.session_state.form_cache.get("machine_type")),
            ("Disk Usage", st.session_state.form_cache.get("disk_usage")),
            ("Capacity", f"{cap} GiB" if cap is not None else "100 GiB"),
        ]
    elif step_num == 5:
        pk = st.session_state.form_cache.get("perf_known", "No")
        pk_str = "Yes" if pk == "Yes" else "No"
        pairs = [("Performance Known", pk_str)]
        if pk == "Yes":
            iops = st.session_state.form_cache.get("iops")
            tput = st.session_state.form_cache.get("throughput")
            rw = st.session_state.form_cache.get("rw_pattern", "Mixed")
            pairs.append(("IOPS", f"{iops:,}" if iops else "0"))
            pairs.append(("Throughput", f"{tput} MiB/s" if tput else "0 MiB/s"))
            pairs.append(("R/W Pattern", rw))
    elif step_num == 6:
        pairs = [("Preference", st.session_state.form_cache.get("preference"))]

    html = _summary_rows(pairs)
    if html:
        st.markdown(html, unsafe_allow_html=True)


def _validate_step(step_num: int) -> bool:
    if step_num == 1:
        v = st.session_state.form_cache.get("workload")
        if not v or v == _PLACEHOLDER:
            st.error("Please select a Workload Category before proceeding.")
            return False
        v = st.session_state.form_cache.get("app_type")
        if not v or v == _PLACEHOLDER:
            st.error("Please select an Application Type before proceeding.")
            return False
    elif step_num == 2:
        v = st.session_state.form_cache.get("deployment")
        if not v or v == _PLACEHOLDER:
            st.error("Please select a Deployment Model before proceeding.")
            return False
        dep = v
        wc = st.session_state.form_cache.get("workload")
        if dep == "Multi Zone":
            ha = st.session_state.form_cache.get("ha_required")
            if not ha or ha == _PLACEHOLDER:
                st.error("Please specify whether HA replication is needed.")
                return False
        if wc == "AI/ML" and dep != "Multi Region":
            sr = st.session_state.form_cache.get("shared_read")
            if not sr or sr == _PLACEHOLDER:
                st.error("Please specify whether shared-read access is required.")
                return False
    elif step_num == 3:
        v = st.session_state.form_cache.get("environment")
        if not v or v == _PLACEHOLDER:
            st.error("Please select an Environment before proceeding.")
            return False
        v = st.session_state.form_cache.get("dr_strategy")
        if not v or v == _PLACEHOLDER:
            st.error("Please select a DR Strategy before proceeding.")
            return False
    elif step_num == 4:
        v = st.session_state.form_cache.get("machine_type")
        if not v or v == _PLACEHOLDER:
            st.error("Please select a Machine Type Tier before proceeding.")
            return False
        v = st.session_state.form_cache.get("disk_usage")
        if not v or v == _PLACEHOLDER:
            st.error("Please select a Disk Usage before proceeding.")
            return False
    elif step_num == 5:
        v = st.session_state.form_cache.get("perf_known")
        if not v or v == _PLACEHOLDER:
            st.error("Please specify whether performance requirements are known.")
            return False
    elif step_num == 6:
        v = st.session_state.form_cache.get("preference")
        if not v or v == _PLACEHOLDER:
            st.error("Please select a Preference before proceeding.")
            return False
    return True


def _build_inputs() -> WorkloadInput:
    dep = st.session_state.form_cache.get("deployment")
    wc_val = st.session_state.form_cache.get("workload")

    shared_read_val = None
    if wc_val == "AI/ML" and dep and dep != "Multi Region":
        sr_val = st.session_state.form_cache.get("shared_read")
        shared_read_val = sr_val == "Yes" if sr_val is not None else None

    dr_strat_val = st.session_state.form_cache.get("dr_strategy", "None") or "None"
    dr_req = dr_strat_val not in ("None",)
    ha_val = st.session_state.form_cache.get("ha_required")
    ha_sz = st.session_state.form_cache.get("ha_single_zone")

    # Defence in depth against stale conditional state (see _fc_sync).
    # This mirrors EXACTLY when the UI offers the Multi-Writer checkbox:
    #   Single Zone  -> always offered
    #   Multi Zone   -> only when HA == "Yes"
    #   Multi Region -> never offered
    # Outside those cases a True was never something the user could see or
    # untick, so it is leftover state rather than intent — and it silently
    # triggers C-01, eliminating five of eight disk types.
    #
    # Keep this even with the _fc_sync clears in place. The callback fix
    # depends on Streamlit's widget lifecycle; this does not. It is the
    # assertion that the invariant holds no matter how state got here.
    multi_writer_val = bool(st.session_state.form_cache.get("multi_writer", False))
    if dep == "Multi Zone":
        if ha_val != "Yes":
            multi_writer_val = False
    elif dep != "Single Zone":
        multi_writer_val = False

    if dep == "Single Zone" and multi_writer_val:
        ha_sz = True

    perf_known_bool = st.session_state.form_cache.get("perf_known") == "Yes"
    iops_val = st.session_state.form_cache.get("iops") if perf_known_bool else None
    tput_val = st.session_state.form_cache.get("throughput") if perf_known_bool else None
    rw_val = st.session_state.form_cache.get("rw_pattern") if perf_known_bool else "Mixed"

    return WorkloadInput(
        workload_category=st.session_state.form_cache.get("workload", ""),
        application_type=st.session_state.form_cache.get("app_type", ""),
        deployment_model=dep,
        multi_writer=multi_writer_val,
        shared_read=shared_read_val,
        environment=st.session_state.form_cache.get("environment", "Prod"),
        dr_strategy=dr_strat_val,
        machine_type_tier=st.session_state.form_cache.get("machine_type", "General Purpose"),
        disk_usage=st.session_state.form_cache.get("disk_usage", "Data Only"),
        capacity_gib=st.session_state.form_cache.get("capacity") or 100,
        iops=iops_val,
        throughput_mibs=tput_val,
        preference=st.session_state.form_cache.get("preference", "Balanced"),
        read_write_pattern=rw_val,
        ha_required=(ha_val == "Yes") if ha_val is not None else None,
        ha_required_single_zone=ha_sz,
        dr_required=dr_req,
        performance_known=perf_known_bool,
        deployment_state=st.session_state.form_cache.get("deploy_state", "New Deployment"),
        current_disk_type=st.session_state.form_cache.get("current_disk"),
    )


def _render_current_step(step_num: int) -> None:
    if step_num == 1:
        section_header(1)

        _maybe_restore_cache("workload")
        st.selectbox("Workload Category",
                     options=[_PLACEHOLDER] + list(_APP_TYPES.keys()),
                     index=0, key="workload", on_change=_fc_sync("workload"),
                     help="Broad category of workload (e.g. Database, AI/ML). Determines "
                          "which application types are offered next and which scoring "
                          "rules apply.")

        app_types = _APP_TYPES.get(st.session_state.workload, [])
        if app_types:
            _maybe_restore_cache("app_type")
            st.selectbox("Application Type",
                         options=[_PLACEHOLDER] + app_types,
                         index=0, key="app_type", on_change=_fc_sync("app_type"),
                         help="The specific application or platform within the category. "
                              "Refines the recommendation with app-specific rules (e.g. "
                              "SAP HANA vs. a generic database).")

        _maybe_restore_cache("deploy_state")
        st.selectbox("Deployment State", options=_DEPLOY_STATES, key="deploy_state",
                     on_change=_fc_sync("deploy_state"),
                     help="Whether this is a brand-new deployment or a migration of an "
                          "existing workload. Migrations let you record your current disk "
                          "type for reference.")
        if st.session_state.deploy_state == "Existing Migration":
            _maybe_restore_cache("current_disk")
            st.selectbox("Current Disk Type", options=[_PLACEHOLDER,
                "Standard PD (pd-standard)", "Balanced PD (pd-balanced)",
                "SSD PD (pd-ssd)", "Extreme PD (pd-extreme)",
            ], index=0, key="current_disk",
            on_change=_fc_sync("current_disk"),
            help="The disk type you're migrating from. Recorded for reference only — "
                 "it doesn't affect the recommendation.")

    elif step_num == 2:
        section_header(2)

        _maybe_restore_cache("deployment")
        st.selectbox("Deployment Model", options=_DEPLOYMENTS,
                     index=0, key="deployment",
                     on_change=_fc_sync("deployment", clears=(
                         "multi_writer", "ha_required", "shared_read", "ha_single_zone")),
                     help="Single Zone (one zone), Multi Zone (spans zones within a region), "
                          "or Multi Region (spans regions). Block storage can't span regions "
                          "natively, so Multi Region relies on your DR strategy instead.")

        dep = st.session_state.deployment

        if dep == "Single Zone":
            _maybe_restore_cache("multi_writer")
            st.checkbox("Multi-Writer Access required", key="multi_writer",
                        on_change=_fc_sync("multi_writer"),
                        help="Whether multiple VMs need concurrent read-write access to the "
                             "same disk (e.g. a clustered database). Limits candidates to "
                             "disk types that support multi-writer mode.")
            if st.session_state.multi_writer:
                st.caption(":information_source: Single-zone HA will be configured via multi-writer.")
        elif dep == "Multi Zone":
            _maybe_restore_cache("ha_required")
            ha_val = st.radio("GCP cross-zone replication (HA) needed",
                              [_PLACEHOLDER, "No", "Yes"], index=0,
                              horizontal=True, key="ha_required",
                              on_change=_fc_sync("ha_required", clears=("multi_writer",)),
                              help="Whether the disk itself should synchronously replicate "
                                   "across two zones for automatic failover (Hyperdisk "
                                   "Balanced HA / Regional PD). If No, HA is assumed to be "
                                   "handled at the application layer instead.")
            if ha_val == "Yes":
                _maybe_restore_cache("multi_writer")
                st.checkbox("Multi-Writer required", key="multi_writer",
                            on_change=_fc_sync("multi_writer"),
                            help="Within your cross-zone HA setup, whether multiple VMs need "
                                 "to write concurrently (active-active clustering) rather "
                                 "than simple failover (active-passive replication).")
        elif dep == "Multi Region":
            st.info("Note: GCP block storage cannot span regions. DR profiling applies below.")

        if st.session_state.workload == "AI/ML" and dep and dep != "Multi Region":
            _maybe_restore_cache("shared_read")
            st.radio("Concurrent Shared-Read Access required",
                     [_PLACEHOLDER, "No", "Yes"], index=0,
                     horizontal=True, key="shared_read",
                     on_change=_fc_sync("shared_read"),
                     help="Whether multiple VMs need simultaneous read-only access "
                          "to the same volume, e.g. serving a shared model or "
                          "dataset to a training/inference cluster.")

    elif step_num == 3:
        section_header(3)

        _maybe_restore_cache("environment")
        st.selectbox("Environment", options=_ENVIRONMENTS, key="environment",
                     on_change=_fc_sync("environment"),
                     help="Deployment tier. Production workloads are scored more "
                          "conservatively than Dev/Test/UAT.")
        _maybe_restore_cache("dr_strategy")
        st.selectbox("DR Strategy", options=_DR_STRATEGIES, key="dr_strategy",
                     on_change=_fc_sync("dr_strategy"),
                     help="Your disaster recovery approach, if any. Choosing anything other "
                          "than None flags the workload as requiring DR-aware validation.")

    elif step_num == 4:
        section_header(4)

        _maybe_restore_cache("machine_type")
        st.selectbox("Machine Type Tier", options=_MACHINE_TYPES, key="machine_type",
                     on_change=_fc_sync("machine_type"),
                     help="The Compute Engine machine family you plan to use. Some Hyperdisk "
                          "types are restricted to specific machine families.")
        _maybe_restore_cache("disk_usage")
        st.selectbox("Disk Usage", options=_DISK_USAGES, key="disk_usage",
                     on_change=_fc_sync("disk_usage"),
                     help="Whether this volume is a boot disk, data disk, or both. Hyperdisk "
                          "ML is data-only and is excluded for boot-disk use cases.")
        _maybe_restore_cache("capacity")
        st.number_input("Capacity (GiB)", min_value=1, value=100, step=100, key="capacity",
                        on_change=_fc_sync("capacity"),
                        help="Requested volume size. Checked against each disk "
                             "type's maximum single-volume capacity.")

    elif step_num == 5:
        section_header(5)

        _maybe_restore_cache("perf_known")
        perf = st.radio("Do you know your performance requirements (IOPS / throughput)?",
                        [_PLACEHOLDER, "No", "Yes"], index=0,
                        horizontal=True, key="perf_known",
                        on_change=_fc_sync("perf_known", clears=("iops", "throughput", "rw_pattern")),
                        help="If you know your target IOPS/throughput, the engine hard-checks "
                             "candidates against each disk type's limits. If not, the "
                             "recommendation is based on workload profile alone and flagged "
                             "as unverified.")

        if perf == "Yes":
            _maybe_restore_cache("iops")
            st.number_input("Target IOPS", min_value=0, value=5000, step=1000, key="iops",
                            on_change=_fc_sync("iops"),
                            help="Peak IOPS (I/O operations per second) you expect "
                                 "the workload to need. Compared against each disk "
                                 "type's max IOPS.")
            _maybe_restore_cache("throughput")
            st.number_input("Target Throughput (MiB/s)", min_value=0, value=200, step=50, key="throughput",
                            on_change=_fc_sync("throughput"),
                            help="Peak throughput you expect the workload to need, "
                                 "in MiB/s. Compared against each disk type's max "
                                 "throughput.")
            _maybe_restore_cache("rw_pattern")
            st.selectbox("Read/Write Pattern", options=_RW_PATTERNS, key="rw_pattern",
                         on_change=_fc_sync("rw_pattern"),
                         help="Whether the workload is read-heavy, write-heavy, or "
                              "mixed. Used as an additional scoring signal for some "
                              "workload types.")

    elif step_num == 6:
        section_header(6)

        _maybe_restore_cache("preference")
        st.selectbox("Recommendation Preference", options=_PREFERENCES, key="preference",
                     on_change=_fc_sync("preference"),
                     help="Biases the final ranking toward lower cost, balance, or maximum "
                          "performance when multiple disk types are otherwise close in score.")


def _render_result(result: dict) -> None:
    winner = result["winner"]
    winner_display = result["winner_display"]
    winner_raw = result["winner_raw"]
    ranking = result["ranked"]
    constraints = result["constraints"]
    excluded = result["excluded"]
    penalty_excluded = result["penalty_excluded"]
    warnings = result["warnings"]
    inputs = result["inputs"]

    if not winner and not ranking:
        st.warning("No eligible disk types found for this workload.")
        if excluded:
            st.markdown(f"**All candidates excluded:** {', '.join(excluded)}")
        return

    html_class = "winner-card"
    fallback_tag = ""
    if result.get("fallback"):
        fallback_tag = " (Fallback — all candidates penalty-excluded)"

    st.markdown(
        f'<div class="{html_class}">'
        f'<p style="font-size:0.9rem;opacity:0.8;">Recommended Disk Type{fallback_tag}</p>'
        f'<h2 style="margin:0;">{winner}</h2>'
        f'<p>Confidence: {winner_display}% &nbsp;|&nbsp; Raw Score: {winner_raw}</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if ranking:
        st.markdown("**Ranked Candidates**")
        sorted_ranking = sorted(
            ranking.items(),
            key=lambda x: CANONICAL_ORDER.index(x[0]) if x[0] in CANONICAL_ORDER else 999,
        )
        sorted_ranking.sort(key=lambda x: -x[1]["raw"])
        for rank, (dt, data) in enumerate(sorted_ranking, 1):
            is_winner = "winner-row" if dt == winner else ""
            st.markdown(
                f'<div class="rank-entry {is_winner}">'
                f'<span>{rank}. <strong>{dt}</strong>'
                f'{" &nbsp;\U0001f3c6" if is_winner else ""}'
                f'</span>'
                f'<span class="score">{data["display"]}%</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    if warnings:
        st.markdown("**Why this recommendation was selected**")
        reasons = []
        if inputs.environment == "Prod":
            reasons.append("Suitable for production environments")
        if inputs.capacity_gib >= 1000:
            reasons.append("Supports the requested storage capacity")
        if inputs.iops:
            reasons.append(f"Meets the requested performance target of {inputs.iops:,} IOPS")
        if inputs.throughput_mibs:
            reasons.append(f"Supports the requested throughput of {inputs.throughput_mibs:,} MiB/s")

        for reason in reasons:
            st.markdown(f"\u2022 {reason}")

        st.markdown("**Warnings & Notes**")

        st.markdown("**Implementation Validation Checklist**")
        checks = [
            "Verify disk type availability in the target GCP region",
            "Verify disk and machine-series compatibility",
            "Confirm the machine type can achieve the required IOPS and throughput",
            "Validate HA and disaster recovery requirements",
            "Validate backup and snapshot requirements",
        ]
        for check in checks:
            st.markdown(f"\u2705 {check}")

    with st.expander("Workload Summary"):
        def _v(val, fmt=None):
            if val is None or val == "" or val is False:
                return "—"
            if isinstance(val, bool):
                return "Yes"
            if fmt:
                return fmt(val)
            return str(val)

        i = inputs
        html = f"""
        <div class="summary-box" style="padding:0.75rem;">
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px 20px;">

            <div>
              <div style="font-weight:700;color:#4FC3F7;font-size:0.85rem;border-bottom:1px solid #333;padding-bottom:4px;margin-bottom:6px;">Profile</div>
              <span class="summary-label">Category</span><br>
              <span class="summary-value">{_v(i.workload_category)}</span><br>
              <span class="summary-label">Application Type</span><br>
              <span class="summary-value">{_v(i.application_type)}</span><br>
              <span class="summary-label">Deployment State</span><br>
              <span class="summary-value">{_v(i.deployment_state)}</span><br>
              <span class="summary-label">Current Disk Type</span><br>
              <span class="summary-value">{_v(i.current_disk_type)}</span>
            </div>

            <div>
              <div style="font-weight:700;color:#4FC3F7;font-size:0.85rem;border-bottom:1px solid #333;padding-bottom:4px;margin-bottom:6px;">Architecture</div>
              <span class="summary-label">Deployment Model</span><br>
              <span class="summary-value">{_v(i.deployment_model)}</span><br>
              <span class="summary-label">Multi-Writer Access</span><br>
              <span class="summary-value">{_v(i.multi_writer)}</span><br>
              <span class="summary-label">Cross-Zone HA</span><br>
              <span class="summary-value">{_v(i.ha_required)}</span><br>
              <span class="summary-label">DR Strategy</span><br>
              <span class="summary-value">{_v(i.dr_strategy)}</span>
            </div>

            <div>
              <div style="font-weight:700;color:#4FC3F7;font-size:0.85rem;border-bottom:1px solid #333;padding-bottom:4px;margin-bottom:6px;">Infra & Perf</div>
              <span class="summary-label">Environment</span><br>
              <span class="summary-value">{_v(i.environment)}</span><br>
              <span class="summary-label">Machine Type Tier</span><br>
              <span class="summary-value">{_v(i.machine_type_tier)}</span><br>
              <span class="summary-label">Disk Usage</span><br>
              <span class="summary-value">{_v(i.disk_usage)}</span><br>
              <span class="summary-label">Capacity (GiB)</span><br>
              <span class="summary-value">{_v(i.capacity_gib)}</span><br>
              <span class="summary-label">Target IOPS</span><br>
              <span class="summary-value">{_v(i.iops, lambda x: f'{x:,}')}</span><br>
              <span class="summary-label">Target Throughput</span><br>
              <span class="summary-value">{_v(i.throughput_mibs, lambda x: f'{x} MiB/s')}</span>
            </div>

          </div>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)
        if result.get("fallback"):
            st.warning("This recommendation is a fallback — all regular candidates were penalty-excluded.")


# ── Progressive Flow ──────────────────────────────────────────────────────

current_step = st.session_state.step

if current_step > 6:
    st.markdown("### \U0001f4cb Review Your Inputs")
    st.caption("All 6 steps completed. Review your inputs below before generating the recommendation.")

    for s in range(1, 7):
        st.markdown(
            f'<div class="section-header" style="opacity:0.7;font-size:0.9rem;'
            f'margin-top:0.5rem;">\u2713 Step {s} \u2014 {_SECTION_HELP[s][0]}</div>',
            unsafe_allow_html=True,
        )
        _render_step_summary(s)

    st.divider()

    if "result" not in st.session_state:
        if st.button("\U0001f680 Generate Recommendation", type="primary", use_container_width=True):
            _sync_all_cached()
            inputs = _build_inputs()
            result = recommend(inputs)
            st.session_state.result = result
            st.rerun()
    else:
        _render_result(st.session_state.result)
        if st.button("\u2190 Start Over", type="secondary", use_container_width=True):
            _clear_result()
            st.session_state.step = 1
            st.rerun()

else:
    for s in range(1, current_step):
        col1, col2 = st.columns([10, 1])
        with col1:
            st.markdown(
                f'<div class="section-header" style="opacity:0.7;font-size:0.9rem;'
                f'margin-top:0.5rem;">\u2713 Step {s} \u2014 {_SECTION_HELP[s][0]}</div>',
                unsafe_allow_html=True,
            )
            _render_step_summary(s)
        with col2:
            if st.button("Edit", key=f"edit_{s}",
                         help="Go back to this step to change your answer"):
                _sync_all_cached()
                _clear_result()
                st.session_state.step = s
                st.rerun()

    _render_current_step(current_step)

    col1, col2 = st.columns([1, 1])
    with col1:
        if current_step > 1:
            if st.button("\u2190 Back", key=f"back_{current_step}", use_container_width=True):
                _sync_all_cached()
                _clear_result()
                st.session_state.step = current_step - 1
                st.rerun()
    with col2:
        next_label = "Next \u2192" if current_step < 6 else "Review \u2192"
        if st.button(next_label, key=f"next_{current_step}", type="primary", use_container_width=True):
            if _validate_step(current_step):
                _sync_all_cached()
                st.session_state.step = current_step + 1
                st.rerun()
