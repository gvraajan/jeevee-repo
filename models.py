"""Pydantic v2 models defining the schema of every generated artifact.

These models are the *contract* between the Knowledge Builder and the Cloud Run
recommendation engine. If Cloud Run and the builder share this file (or a copy),
the JSON is guaranteed to be structurally valid on both ends.

Design principles
-----------------
* Every knowledge object carries an optional ``source`` so recommendations are
  auditable ("where did this fact come from?").
* Models use ``extra="forbid"`` so a typo in curated YAML fails loudly at build
  time instead of silently producing a broken catalog.
* Fields that GCP will inevitably add later are modelled as open dicts/lists so
  the schema can grow without a breaking change.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Shared
# --------------------------------------------------------------------------- #
class Sourced(_Base):
    """Mixin-style base for anything that should cite where it came from."""

    source: str | None = Field(
        default=None,
        description="URL or identifier of where this fact originated (API or doc).",
    )
    as_of: str | None = Field(
        default=None,
        description="Date the fact was last verified against Google docs (ISO).",
    )


class Performance(_Base):
    """Performance envelope for a disk type. All values are 'up to' ceilings."""

    max_iops_read: int | None = None
    max_iops_write: int | None = None
    max_throughput_read_mbps: int | None = None
    max_throughput_write_mbps: int | None = None
    provisioned_iops: bool = Field(
        default=False, description="Whether IOPS can be provisioned independently."
    )
    provisioned_throughput: bool = Field(
        default=False,
        description="Whether throughput can be provisioned independently.",
    )


# --------------------------------------------------------------------------- #
# catalog.json
# --------------------------------------------------------------------------- #
class Metadata(_Base):
    knowledge_version: str
    builder_version: str
    generated_at: str
    documentation_as_of: str
    mode: Literal["offline", "live"]
    data_sources: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(
        default_factory=dict,
        description="Object counts (regions, zones, machine_types, ...).",
    )


class Zone(_Base):
    name: str
    region: str
    status: str = "UP"


class Region(_Base):
    name: str
    description: str | None = None
    location: str | None = None
    status: str = "UP"
    zones: list[str] = Field(default_factory=list)


class MachineFamily(Sourced):
    name: str = Field(description="Family key, e.g. 'c3', 'n2', 'm3'.")
    display_name: str
    category: Literal[
        "general-purpose", "compute-optimized", "memory-optimized",
        "accelerator-optimized", "storage-optimized",
    ]
    description: str | None = None
    supported_disk_types: list[str] = Field(default_factory=list)
    hyperdisk_support: list[str] = Field(
        default_factory=list,
        description="Subset of supported_disk_types that are Hyperdisk.",
    )
    boot_disk_types: list[str] = Field(default_factory=list)
    max_disks: int | None = None
    notes: list[str] = Field(default_factory=list)


class MachineType(_Base):
    name: str = Field(description="Full machine type, e.g. 'c3-standard-8'.")
    family: str
    guest_cpus: int | None = None
    memory_mb: int | None = None
    zones: list[str] = Field(
        default_factory=list, description="Zones where this type is available."
    )
    source: str | None = None


class DiskType(Sourced):
    name: str = Field(description="API name, e.g. 'pd-balanced', 'hyperdisk-extreme'.")
    display_name: str
    category: Literal["persistent-disk", "hyperdisk", "local-ssd"]
    description: str | None = None
    is_boot_eligible: bool = False
    supports_regional: bool = False
    supports_multi_writer: bool = False
    min_size_gb: int | None = None
    max_size_gb: int | None = None
    performance: Performance = Field(default_factory=Performance)
    restrictions: list[str] = Field(default_factory=list)


class CompatibilityRule(Sourced):
    """A hard compatibility fact, e.g. 'hyperdisk-ml cannot be a boot disk'."""

    id: str
    statement: str
    applies_to: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured selector, e.g. {'disk_type': 'hyperdisk-ml'}.",
    )


class Catalog(_Base):
    metadata: Metadata
    regions: list[Region] = Field(default_factory=list)
    zones: list[Zone] = Field(default_factory=list)
    machine_families: list[MachineFamily] = Field(default_factory=list)
    machine_types: list[MachineType] = Field(default_factory=list)
    disk_types: list[DiskType] = Field(default_factory=list)
    compatibility_rules: list[CompatibilityRule] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# rules.json
# --------------------------------------------------------------------------- #
class Operator(str, Enum):
    eq = "eq"
    ne = "ne"
    in_ = "in"
    not_in = "not_in"
    gte = "gte"
    lte = "lte"
    gt = "gt"
    lt = "lt"
    exists = "exists"
    contains = "contains"


class Condition(_Base):
    field: str = Field(description="Answer key to test, e.g. 'workload'.")
    op: Operator
    value: Any | None = None


class ConditionGroup(_Base):
    """Boolean group. Exactly one of all_/any_ should be populated.

    Groups nest, so arbitrarily complex logic is expressible in data alone --
    the Cloud Run engine just walks the tree.
    """

    all_: list["ConditionGroup | Condition"] | None = Field(default=None, alias="all")
    any_: list["ConditionGroup | Condition"] | None = Field(default=None, alias="any")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Recommendation(_Base):
    disk_type: str
    confidence: Literal["high", "medium", "low"] = "medium"
    rationale: str
    alternatives: list[str] = Field(default_factory=list)


class Rule(Sourced):
    id: str
    description: str
    priority: int = Field(
        default=50,
        description="Higher wins when multiple rules match. 0-100 convention.",
    )
    when: ConditionGroup | Condition
    recommend: Recommendation
    tags: list[str] = Field(default_factory=list)


class RulesFile(_Base):
    metadata: Metadata
    default_recommendation: Recommendation
    rules: list[Rule] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# questions.json
# --------------------------------------------------------------------------- #
class QuestionType(str, Enum):
    single_select = "single_select"
    multi_select = "multi_select"
    number = "number"
    boolean = "boolean"
    text = "text"


class Option(_Base):
    value: str
    label: str
    help_text: str | None = None


class Question(_Base):
    id: str
    order: int
    text: str
    type: QuestionType
    maps_to: str = Field(description="Answer key this question fills for rules.")
    required: bool = True
    help_text: str | None = None
    options: list[Option] = Field(default_factory=list)
    options_from: str | None = Field(
        default=None,
        description=(
            "Dynamic option source resolved by Cloud Run from the catalog, e.g. "
            "'catalog.regions' or 'catalog.disk_types'. Keeps the questionnaire "
            "future-proof: new regions/disks appear without editing YAML."
        ),
    )
    depends_on: ConditionGroup | Condition | None = Field(
        default=None,
        description="Only show this question when the condition is satisfied.",
    )


class QuestionsFile(_Base):
    metadata: Metadata
    questions: list[Question] = Field(default_factory=list)


# Resolve the forward reference for the self-referential ConditionGroup.
ConditionGroup.model_rebuild()
