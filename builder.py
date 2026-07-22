"""GCP Storage Advisor -- Knowledge Builder entrypoint.

Orchestrates collectors and generators to produce the three JSON artifacts that
Cloud Run consumes:

    output/catalog.json     -- the knowledge base (API skeleton + curated overlay)
    output/rules.json       -- data-driven recommendation rules
    output/questions.json   -- the questionnaire

Usage
-----
    python builder.py                       # offline mode (uses data snapshot)
    python builder.py --live --project MYPROJ   # live Compute API mode
    python builder.py --only catalog        # regenerate a single artifact
    python builder.py --regions us-central1 europe-west1   # live: limit regions

Nothing here is uploaded automatically. Review output/, then upload to your GCS
bucket manually (by design -- keeps a human in the loop before publishing).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

import config
from collectors.compatibility import CompatibilityIndex
from collectors.compute import ComputeClient
from collectors.disks import DiskCollector
from collectors.documentation import DocumentationCollector
from collectors.machine_types import MachineTypeCollector
from collectors.regions import RegionCollector
from generators.catalog import CatalogGenerator
from generators.questions import QuestionsGenerator
from generators.rules import RulesGenerator
from models import Metadata


class KnowledgeBuilder:
    def __init__(self, settings: config.BuilderSettings, log: logging.Logger) -> None:
        settings.validate()
        self.settings = settings
        self.log = log
        self.client = ComputeClient(settings)
        overlay = DocumentationCollector().load()
        self.compat = CompatibilityIndex(overlay)

    # ------------------------------------------------------------------ #
    def _metadata(self) -> Metadata:
        return Metadata(
            knowledge_version=config.KNOWLEDGE_VERSION,
            builder_version=config.BUILDER_VERSION,
            generated_at=config.utc_now_iso(),
            documentation_as_of=config.DOCUMENTATION_AS_OF,
            mode=self.settings.mode,  # type: ignore[arg-type]
            data_sources=[
                self.client.data_source_label,
                f"curated overlay: {config.COMPATIBILITY_YAML.name}",
            ],
        )

    def _write(self, model, path) -> None:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        payload = model.model_dump(by_alias=True, exclude_none=False)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        self.log.info("Wrote %s (%d bytes)", path, path.stat().st_size)

    # ------------------------------------------------------------------ #
    def build_catalog(self) -> None:
        self.log.info("--- Building catalog.json ---")
        regions = RegionCollector(self.client).collect()
        zone_names = [z["name"] for z in regions["zones"]]

        machine_types = MachineTypeCollector(self.client).collect(zone_names)
        disk_types = DiskCollector(self.client).collect(zone_names)

        catalog = CatalogGenerator(self.compat).build(
            regions_raw=regions["regions"],
            zones_raw=regions["zones"],
            machine_types_raw=machine_types,
            disk_types_raw=disk_types,
            metadata=self._metadata(),
        )
        self._write(catalog, config.CATALOG_PATH)

    def build_rules(self) -> None:
        self.log.info("--- Building rules.json ---")
        rules = RulesGenerator().build(self._metadata())
        self._write(rules, config.RULES_PATH)

    def build_questions(self) -> None:
        self.log.info("--- Building questions.json ---")
        questions = QuestionsGenerator().build(self._metadata())
        self._write(questions, config.QUESTIONS_PATH)

    def build_all(self, only: str | None) -> None:
        targets = {
            "catalog": self.build_catalog,
            "rules": self.build_rules,
            "questions": self.build_questions,
        }
        chosen = [targets[only]] if only else list(targets.values())
        for fn in chosen:
            fn()
        self.log.info("Done. Artifacts in %s", config.OUTPUT_DIR)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GCP Storage Advisor knowledge builder")
    p.add_argument("--live", action="store_true",
                   help="Use the live Compute API instead of the offline snapshot.")
    p.add_argument("--project", help="GCP project id (required with --live).")
    p.add_argument("--regions", nargs="*", default=[],
                   help="Limit live collection to these regions.")
    p.add_argument("--only", choices=["catalog", "rules", "questions"],
                   help="Build only one artifact.")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    log = config.configure_logging(args.log_level)
    settings = config.BuilderSettings(
        mode="live" if args.live else "offline",
        project_id=args.project or config.BuilderSettings().project_id,
        region_filter=args.regions,
        log_level=args.log_level,
    )
    try:
        KnowledgeBuilder(settings, log).build_all(args.only)
    except Exception:
        log.exception("Build failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
