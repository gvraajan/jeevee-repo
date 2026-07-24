# GCP Disk Advisor — Scoring Engine

## Inputs

- **WorkloadInput** dataclass captures: workload category, app type, deployment model, multi-writer, HA/DR flags, environment, machine type, disk usage, capacity, IOPS, throughput, read/write pattern, preference
- `to_context()` normalises everything into a flat `dict` — opt-in flags like HA/DR become `"Yes"`/`"No"` (never blank), required enums pass through as-is (blank stays missing)
- **Missing value rule (F2):** an absent field satisfies no condition — `eq`, `ne`, `in`, and numeric comparisons all return `false` when the field is missing

## Pipeline (4 phases)

1. **Constraint elimination** — hard-exclude disk types
   - Static phase: dictionary lookup for `multiWriter`, `appType`, `diskUsage`, `machineType` — zero condition-evaluator overhead
   - Dynamic phase: compound conditions (and/or/neq) evaluated with `_fail_closed=True` so missing fields still cause eliminations
   - Eliminated disks never enter scoring

2. **Scoring** — accumulate raw points per surviving disk type
   - ~60 scored rules (DB-xx, EA-xx, WA-xx, MOD-xx, PERF-xx, PREF-xx, etc.)
   - Each rule: `condition + diskType + points`. If condition matches, `points` are added to that disk type's `raw` tally
   - Points can be negative (e.g. `PREF-02` — Cost First penalises Hyperdisk Extreme with −25)
   - Multiple rules can fire for the same disk type (stacking)

3. **Penalty exclusion** — remove disks that can't deliver
   - **Capability check:** requested IOPS/throughput/capacity compared against `DISK_CAPABILITIES` max values. If exceeded → excluded permanently (`exclusion_kind: "capability"`)
   - **Score check:** raw ≤ 0 → excluded (`exclusion_kind: "score"`) — this is a preference judgement, reversible by fallback

4. **Ranking & confidence** — order survivors, compute display%
   - Tie-break key: `( -raw, -best_classification_rank, CANONICAL_ORDER_index )`
   - Confidence = **relative margin** between #1 and #2:
     - `base = 55 + 35 × (top − second) / top` → range 55–90
     - Capped at 75 if performance requirements not provided
     - Floored at 50, ceiling at 95
   - Display % for runner-ups = `(raw / top_raw) × confidence` (proportional, ≥ 1)

## Fallback

- When **every** candidate is excluded but at least one was score-excluded (not capability-excluded), the highest-raw score-excluded disk is resurrected at 50% display with a `W-FB` warning
- When **all** are capability-excluded, the result is `infeasible` — no recommendation, `W-INF` warning suggests striping or splitting the workload

## Final output

```
{
  winner, winner_display (%), winner_raw,
  ranked: [{ diskType → { raw, rules[], display% } }],
  constraints: [triggered constraint rules],
  excluded: [disk types eliminated by constraints],
  penalty_excluded: [capability/score failures],
  warnings, fallback, infeasible
}
```
