"""Central configuration for the GCP Storage Advisor knowledge builder.

Everything that a future maintainer might need to change lives here:
version numbers, file paths, the documentation "as of" date, and the
list of GCP APIs the collectors touch. No knowledge or rules live here --
only wiring.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Versioning
# --------------------------------------------------------------------------- #
# Bump BUILDER_VERSION when the *code* changes in a way that affects output.
BUILDER_VERSION = "1.0.0"

# Bump KNOWLEDGE_VERSION when the *curated knowledge* (YAML overlays) changes.
# This is the version surfaced in the Cloud Run UI so users know how fresh the
# advice is. Use a simple date-based scheme: YYYY.MM.N
KNOWLEDGE_VERSION = "2026.01.1"

# The month/year of the Google Cloud documentation the curated overlay reflects.
# Surfaced verbatim in the UI: "Based on Google Cloud documentation as of ...".
DOCUMENTATION_AS_OF = "January 2026"


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
KNOWLEDGE_DIR = ROOT / "knowledge"          # human-curated YAML (source of truth)
DATA_DIR = ROOT / "data"                     # offline API snapshot
OUTPUT_DIR = ROOT / "output"                 # generated JSON goes here
LOG_DIR = ROOT / "logs"

CATALOG_PATH = OUTPUT_DIR / "catalog.json"
RULES_PATH = OUTPUT_DIR / "rules.json"
QUESTIONS_PATH = OUTPUT_DIR / "questions.json"

OFFLINE_SNAPSHOT_PATH = DATA_DIR / "offline_snapshot.json"

COMPATIBILITY_YAML = KNOWLEDGE_DIR / "compatibility.yaml"
RULES_YAML = KNOWLEDGE_DIR / "rules.yaml"
QUESTIONS_YAML = KNOWLEDGE_DIR / "questions.yaml"


# --------------------------------------------------------------------------- #
# Runtime settings
# --------------------------------------------------------------------------- #
@dataclass
class BuilderSettings:
    """Runtime settings, overridable via CLI flags or environment variables."""

    # "offline" uses data/offline_snapshot.json. "live" calls the Compute API.
    mode: str = os.getenv("BUILDER_MODE", "offline")

    # GCP project used when mode == "live". Required only in live mode.
    project_id: str | None = os.getenv("GOOGLE_CLOUD_PROJECT")

    # Optional: restrict live collection to these regions (speeds up dev runs).
    # Empty list means "all regions".
    region_filter: list[str] = field(default_factory=list)

    log_level: str = os.getenv("BUILDER_LOG_LEVEL", "INFO")

    def validate(self) -> None:
        if self.mode not in ("offline", "live"):
            raise ValueError(f"mode must be 'offline' or 'live', got {self.mode!r}")
        if self.mode == "live" and not self.project_id:
            raise ValueError(
                "live mode requires a GCP project. Set GOOGLE_CLOUD_PROJECT "
                "or pass --project."
            )


def utc_now_iso() -> str:
    """RFC-3339 timestamp used for generation metadata."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_iso() -> str:
    return date.today().isoformat()


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure root logging: console + a timestamped file under logs/."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"builder_{datetime.now():%Y%m%d_%H%M%S}.log"

    fmt = "%(asctime)s | %(levelname)-7s | %(name)-22s | %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(logfile, encoding="utf-8"),
        ],
    )
    log = logging.getLogger("builder")
    log.info("Logging to %s", logfile)
    return log
