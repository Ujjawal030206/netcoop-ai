# NetCoop AI

**Multi-agent network fault diagnosis with an interoperable, schema-validated agent protocol.**

Specialist agents each examine one part of a campus network (traffic performance, device health, topology / Wi-Fi / services), exchange structured findings over a validated message bus, question each other when their explanations compete, and agree on one explainable diagnosis: the probable root cause, the evidence, the affected devices, the alternatives, a confidence score with a published formula, and recommended actions. When the evidence is too thin, the system says so instead of guessing.

```
python -m netcoop diagnose data/scenarios/conflicting_wifi_vs_congestion.json --trace
```

```
QUERY     correlation_agent -> performance_agent   correlate_metrics: check 'link_congestion' on HOSTEL_SW_01
RESPONSE  performance_agent -> correlation_agent   latency on HOSTEL_SW_01 tracks utilisation on its own link (r=1.00, n=6)
QUERY     correlation_agent -> connectivity_agent  scope_check: check 'weak_wifi_signal' on HOSTEL_AP_01
RESPONSE  connectivity_agent -> correlation_agent  wired neighbours are degraded too: the fault is upstream of the access point

Root cause: Likely link congestion causing egress queue drops at HOSTEL_SW_01
Confidence: 0.80 (high)
```

## Results

| Check | Result |
|---|---|
| Bundled scenarios (`python tools/run_scenarios.py`) | **17 / 17** |
| Every campus scenario regenerated with 30 random seeds (`python tools/stress_test.py`) | **330 / 330** |
| Random records from the reference telemetry generator, 100 per type | **300 / 300** |
| Unit, integration and API tests (`pytest`) | **66 passing** |
| Time per diagnosis | ~10–40 ms on a laptop, fully offline |

The diagnosis is deterministic: the same input always gives the same answer. No network connection or API key is needed. An LLM is optional and only rewrites the finished report as a plain-English summary.

## Quick start

```bash
pip install -r requirements.txt
python server.py                     # dashboard at http://127.0.0.1:8000
```

```bash
python -m netcoop diagnose data/scenarios/link_congestion.json            # readable report
python -m netcoop diagnose data/scenarios/weak_wifi.json --trace          # every inter-agent message
python -m netcoop diagnose data/scenarios/weak_wifi.json --crash infrastructure   # robustness demo
python -m netcoop diagnose my_telemetry.json --json report.json --log messages.json
python -m netcoop diagnose - < telemetry.ndjson                           # stdin: JSON, NDJSON or CSV
python -m netcoop scenarios                                               # run all scenarios
pytest -q
```

Python 3.10+. Dependencies: FastAPI + Uvicorn (dashboard), jsonschema (protocol validation), pytest; `openai` and `python-dotenv` only for the optional LLM summary.

## Architecture

```
                 ┌──────────────┐
 raw telemetry ─►│  Ingestion   │  translate any dialect to the canonical schema, score data quality
 JSON/NDJSON/CSV │    agent     │
                 └──────┬───────┘
                        │ TELEMETRY (broadcast)
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
 ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
 │ Performance │ │Infrastructure│ │ Connectivity │      external / legacy agents
 │    agent    │ │    agent     │ │    agent     │      (netcoop-msg/0.9 via adapter)
 └──────┬──────┘ └──────┬───────┘ └──────┬───────┘                │
        │ FINDING       │ FINDING        │ FINDING                │ FINDING
        └───────────────┼────────────────┘◄───────────────────────┘
                        ▼            ▲ QUERY / RESPONSE (follow-up questions)
                 ┌──────────────┐    │
                 │    Fault     │────┘
                 │ Correlation  │  combine evidence, rank causes, resolve conflicts, confidence
                 └──────┬───────┘
                        │ FINDING (ranked diagnosis)
                 ┌──────▼───────┐
                 │  Reporting   │──► REPORT: JSON (+ Markdown, + optional LLM summary)
                 └──────────────┘
```

An **orchestrator** (`netcoop/orchestrator.py`) drives the run by sending TASK messages: normalize → broadcast telemetry → accept external findings → analyze (each specialist) → correlate → report. Every arrow is a message validated against [`schemas/message.schema.json`](schemas/message.schema.json) by the **message bus** (`netcoop/protocol.py`); there are no side channels, so every diagnosis can be replayed and audited from its message log.

### Agent responsibilities

| Agent | Responsibility | Canonical metrics it owns | Follow-up questions it answers |
|---|---|---|---|
| **Ingestion** (interoperability gateway) | Parse JSON / NDJSON / CSV; map field names and units to the canonical schema; reject bad values with reasons; detect stale data; score data quality | all (translation only) | – |
| **Performance** | Traffic experience | `latency_ms`, `jitter_ms`, `packet_loss_pct`, `throughput_mbps`, `bandwidth_mbps`, `link_utilization_pct` | `correlate_metrics` (does delay follow load, on the device or anywhere upstream?), `baseline_connectivity` (is the path healthy?) |
| **Infrastructure** | Device health | `cpu_utilization_pct`, `memory_utilization_pct`, `queue_drops`, `interface_errors`, `crc_errors`, `link_flaps`, `temperature_c`, device status | `device_detail` (hardware failure or overload?), `error_trend` (are errors still rising?) |
| **Connectivity** | Scope, topology, Wi-Fi radio, DNS/DHCP, silent devices | `rssi_dbm`, `snr_db`, `retransmission_pct`, `connected_clients`, `dns_*`, `dhcp_*`, `telemetry_missing`, `downstream_impact` | `scope_check` (Wi-Fi-only or wider? how many devices below a silent device are cut off?) |
| **Fault Correlation** | Combine all findings (internal and external), score every fault on every device, ask follow-up questions, record conflicts, compute confidence | – | – |
| **Reporting** | Build the JSON report and Markdown, recommended actions, limitations; optional LLM summary | – | – |

## Communication interfaces (protocol)

Full reference: [`docs/PROTOCOL.md`](docs/PROTOCOL.md).

Every message uses the **netcoop-msg/1.0** envelope:

```json
{
  "protocol": "netcoop-msg/1.0",
  "msg_id": "msg-3f9c1a2b7d",
  "incident_id": "INC-LINK-CONGESTION",
  "type": "FINDING",
  "sender": "performance_agent",
  "recipient": "correlation_agent",
  "in_reply_to": "msg-91d0e4c2aa",
  "timestamp": "2026-10-31T10:05:00.123Z",
  "payload": { "...": "shape depends on type" }
}
```

Types: `TASK`, `TELEMETRY`, `FINDING`, `QUERY`, `RESPONSE`, `ERROR`, `REPORT`. Each payload type has its own schema (for example a FINDING must carry `observations` with metric, value, **unit**, device, threshold, severity and interpretation, plus `hypotheses` with scores in 0–1, `checked_metrics`, `missing_metrics` and `data_quality`). All values travel in canonical units, so no agent ever guesses whether a number is in seconds or milliseconds.

**Version adapters.** Messages in the legacy `netcoop-msg/0.9` format (`from / to / kind / data` with free-text issues and raw readings) are upgraded before validation: readings go through the same unit converter, and the issue text is mapped to a fault id. External agents join the diagnosis at half weight.

**Rejection.** A message that fails validation is not delivered. The bus logs an `ERROR` naming the exact field (`payload/hypotheses/0/score: 1.7 is greater than the maximum of 1`) and the diagnosis continues without it.

## Interoperability: one vocabulary for every device

The ingestion agent translates every incoming field to one of 21 canonical metrics (`netcoop/catalog.py`), each with a fixed unit and valid range. The reference generator format `{timestamp, device_id, metrics{...}, status}` is accepted unchanged.

| Incoming | Canonical | Rule |
|---|---|---|
| `rtt: "0.25 s"` | `latency_ms = 250` | alias + unit in the value |
| `linkUtil: 0.93` | `link_utilization_pct = 93` | bare 0–1 value read as a fraction (flagged) |
| `pktLossRate: 0.02` | `packet_loss_pct = 2` | "rate" field with a 0–1 value |
| `Jitter: {"value": 12, "unit": "ms"}` | `jitter_ms = 12` | value-with-unit object |
| `signalStrength: "-79 dBm"` | `rssi_dbm = -79` | alias + unit string |
| `ts: 1730000000` / `time: 1730000000000` | ISO 8601 | epoch seconds / milliseconds |
| `latancy_ms` | `latency_ms` | similarity match (warning) |
| `cpuLoad: 140` | rejected | outside 0–100 % (error) |
| `pktLoss: "N/A"` | ignored | missing value (warning) |
| `fanSpeedRpm: 4200` | ignored | unknown meaning: reported, never guessed |

Also handled: NDJSON with corrupt lines, CSV, records without a device id, stale samples (>15 min older than the newest), future timestamps, unknown schema versions (best effort + warning), nested `{"topology": {"nodes": [...], "edges": [...]}}` or `devices/links` topologies with undirected links oriented by device tier, and a `services` block for DNS/DHCP.

Every change is logged as a transformation or an issue, and a **data quality score** (1.0 down to a floor of 0.3) is computed from the number of guesses, rejections, stale samples and version problems. It feeds into the final confidence, so messy data always produces a more cautious diagnosis.

## Evidence correlation and confidence

**1. Grade each reading** against warning / critical thresholds in [`config/thresholds.json`](config/thresholds.json): strength 0 when normal, 0.6 at warning, 0.9 at critical, approaching 1.0 beyond it.

**2. Match against the fault catalogue** in [`config/fault_signatures.json`](config/fault_signatures.json). Each fault lists *primary* metrics (define it), *supporting* symptoms, and *contradicting* readings (point elsewhere), a confirming follow-up question and recommended actions.

| Fault id | Primary | Contradicting |
|---|---|---|
| `link_congestion` | link utilisation, queue drops | interface/CRC errors, device down |
| `device_overload` | CPU, memory | interface errors, device down |
| `hardware_failure` | critical status, interface errors, CPU (2 required) | – |
| `interface_fault` | interface errors, CRC errors, link flaps | high CPU, high utilisation |
| `weak_wifi_signal` | RSSI, SNR, retransmissions | interface errors, high utilisation |
| `dns_failure` | DNS failure rate, DNS response time | network-wide loss / congestion |
| `dhcp_failure` | DHCP failures, pool usage | network-wide loss / congestion |
| `path_latency` | latency, jitter | local congestion, CPU, Wi-Fi, errors, silent parent (checked upstream too) |
| `upstream_device_outage` | silent device + devices below it cut off (2 required) | – |

**3. Score every fault on every device:**

```
primary  = 0.5 × strongest primary + 0.5 × mean primary
support  = mean strength of supporting symptoms on the device or anything below it
base     = 0.75 × primary + 0.25 × support
contra   = strongest contradicting reading (warning counts 40 %, critical counts fully)
score    = (base − 0.4 × contra) × (0.75 + 0.25 × share of primary metrics available)
```

Then +0.05 if two or more agents independently support the fault, a small bonus if an external agent agrees, and **+0.10 / −0.15 from follow-up questions**.

**4. Resolve conflicts.** The correlation agent always checks the top cause and, when the runner-up is within 0.3, checks it too, by sending a QUERY to the specialist that can tell them apart:

| Question | Separates |
|---|---|
| `correlate_metrics` | congestion (delay rises with load on the device or upstream) vs a path / WAN problem |
| `device_detail` | hardware failure (critical status + errors) vs overload (busy CPU, clean interfaces) |
| `error_trend` | an active interface fault vs a historical blip |
| `scope_check` | weak Wi-Fi (wired neighbours healthy) vs a fault upstream of the AP; outage vs monitoring gap |
| `baseline_connectivity` | a real DNS/DHCP fault vs a symptom of a wider network failure |

The report lists each question, its answer, and every conflict with how it was settled (including cases where a specialist's own best guess lost once all evidence was combined).

**5. Confidence:**

```
confidence = top score × (0.65 + 0.35 × margin) × (0.5 + 0.5 × data quality) × 0.85^(missing agents)
margin     = (top − runner-up) / top                     capped at 0.95
```

| Confidence | Category | Wording |
|---|---|---|
| ≥ 0.75 | high | "Likely …" |
| 0.55 – 0.75 | medium | "Probable …" + further verification required |
| 0.35 – 0.55 | low | "Possible (low confidence) …" + verify on site first |
| < 0.35, or only one abnormal reading | insufficient | "Insufficient evidence"; no cause claimed; lists the data to collect |

The margin keeps the system from being confident when two causes score alike; the 0.95 cap reflects that telemetry never proves a cause outright; one abnormal reading is a symptom, not a diagnosis.

## Error handling

| Situation | Behaviour |
|---|---|
| Input is not JSON / NDJSON / CSV | `status: "error"` report with the parser message and position; no crash |
| Corrupt NDJSON line | skipped and reported; the rest is diagnosed |
| Record without device id, bad / missing / out-of-range values, incompatible units | rejected individually with a reason; data quality reduced |
| Stale or future timestamps, unknown schema version | down-weighted or flagged; data quality reduced |
| Device in topology sends nothing | outage only if devices below it are clearly cut off, otherwise a monitoring gap |
| No topology | per-device diagnosis; limitation states that scope is unknown |
| Specialist agent crashes | ERROR message, run continues, confidence × 0.85, limitation names the agent |
| Malformed message from an external agent | rejected by the schema with the exact field |
| Too little evidence | `insufficient_evidence` with the metrics needed |

## Output

Every run produces a JSON report in the required structure (`incident_id`, `status`, `suspected_root_cause`, `confidence`, `affected_devices`, `supporting_evidence`, `alternative_causes`, `recommended_actions`, `evidence_sources`, `limitations`), extended with `root_cause_id`, `confidence_category`, `fault_domain`, `alternatives_detail`, `conflicts_resolved`, `inter_agent_queries`, `confidence_method`, `data_quality` and a plain-English `summary`, plus a Markdown version and the full message log. `status` is one of `diagnosed`, `healthy`, `insufficient_evidence`, `error`.

## Sample data and test scenarios

`data/scenarios/` (regenerate with `python tools/generate_scenarios.py`; expectations in `manifest.json`). The campus model has an internet gateway, core switch, hostel distribution and access switches, two hostel APs and a wired PC, a department switch and PC, a lecture-hall AP, and DNS / DHCP servers, with six one-minute samples per device. Value ranges for `normal`, `latency_spike` and `hardware_failure` follow the reference generator exactly.

| File | Situation | Expected |
|---|---|---|
| `normal.json` | all healthy | healthy |
| `link_congestion.json` | hostel uplink saturates, delay rises with load downstream | `link_congestion` |
| `weak_wifi.json` | one AP with poor signal; wired PC on the same switch fine | `weak_wifi_signal` |
| `interface_fault.json` | CRC / interface errors rising, link flapping | `interface_fault` |
| `dns_failure.json`, `dhcp_failure.json` | service failing while the network path is healthy | `dns_failure`, `dhcp_failure` |
| `device_overload.json` | core CPU and memory exhausted, interfaces clean | `device_overload` |
| `wan_latency.json` | latency spike upstream, no local congestion | `path_latency` |
| `upstream_outage.json` | distribution switch silent, everything below unreachable | `upstream_device_outage` |
| `conflicting_wifi_vs_congestion.json` | borderline AP signal vs a saturated access switch | `link_congestion` after follow-up questions |
| `interop_legacy_mixed.json` | legacy dialect (camelCase, seconds, fractions, epoch times, unit strings), a record without id, an out-of-range value, one valid legacy 0.9 agent message, one malformed message | `link_congestion`, 1 message rejected |
| `organiser_normal.json`, `organiser_latency_spike.json`, `organiser_hardware_failure.json` | single records in the reference generator format | healthy, `path_latency`, `hardware_failure` |
| `organiser_stream.ndjson` | 5 reference-format samples with one corrupt line | `hardware_failure` |
| `insufficient_data.json` | two sparse records | `insufficient_evidence` |
| `malformed_json.txt` | broken JSON | clear `error` report |

## Dashboard and API

`python server.py` serves a dashboard with no external assets (works offline): scenario picker, paste/upload your own telemetry, network map coloured by health with the root cause outlined, an animated diagram of the agents exchanging messages, the full conversation with every message's JSON, evidence with trend sparklines, follow-up questions and conflicts, the confidence calculation with real numbers, the data-translation log, and the JSON report. A switch simulates an agent crash.

| Endpoint | |
|---|---|
| `POST /api/diagnose` | `{"scenario": "weak_wifi.json"}` or `{"telemetry": <any supported input>}`; optional `crash_agents`, `llm` |
| `GET /api/scenarios` | bundled scenarios and expectations |
| `GET /api/schema` | the netcoop-msg/1.0 JSON Schema |
| `GET /api/health` | status and configured faults |

## Extending

* **New fault:** add an entry to `config/fault_signatures.json` (primary, supporting, contradicting metrics, optional `min_primary_abnormal`, `contradict_upstream`, a confirming question and actions). No code change.
* **New thresholds:** edit `config/thresholds.json`.
* **New field dialect:** add stems to `STEMS` in `netcoop/catalog.py`; unknown names are already similarity-matched.
* **New agent:** subclass `AnalysisAgent`, set `owned` metrics, add `answer_<question>` methods, register it in `orchestrator.SPECIALISTS`. External agents only need to send valid netcoop-msg/1.0 FINDINGs.
* **New message version:** add an upgrade function to `protocol.ADAPTERS`.

## Optional LLM summary

Set `LLM_API_KEY` (or `GROQ_API_KEY`), optionally `LLM_BASE_URL` and `LLM_MODEL` (any OpenAI-compatible endpoint) in `.env`, then add `--llm` on the CLI or tick the box in the dashboard. The model only rewrites the finished report for a non-specialist and is told not to add facts; if it fails, the deterministic summary is used.

## Project layout

```
netcoop/
  catalog.py        canonical metrics, units, ranges, field-name stems
  ingest.py         parsing, field/unit translation, validation, data quality
  knowledge.py      thresholds, fault catalogue, reading grades
  scoring.py        evidence index, per-device fault scoring
  protocol.py       envelope, JSON Schema validation, version adapters, message bus
  agents/           base, specialists (performance, infrastructure, connectivity),
                    coordination (ingestion, correlation, reporting)
  orchestrator.py   one diagnosis end to end
  llm.py            optional plain-English summary
  __main__.py       CLI
schemas/message.schema.json
config/thresholds.json, config/fault_signatures.json
data/scenarios/     sample inputs + manifest.json
tools/              generate_scenarios.py, run_scenarios.py, stress_test.py
static/             dashboard
server.py           FastAPI app
tests/              pytest suite
docs/PROTOCOL.md    message and field reference
```

## Limitations

* Thresholds are generic campus values and should be tuned per network.
* Load–latency correlation needs at least four samples per device; single snapshots are diagnosed from levels only (and the report says so).
* Scope analysis depends on topology; without it the diagnosis is per device.
* Evaluated on synthetic telemetry. The system only recommends actions and never changes network devices.
