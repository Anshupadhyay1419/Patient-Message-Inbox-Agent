# Clinic Inbox Agent

A LangGraph capstone for triaging synthetic patient-portal messages. The original brief, requirements and rubric are in [assignment.md](assignment.md). The [design guide](design.md) and [project plan](plan.md) explain the intended architecture and evaluation.

## Setup

Use Python 3.11 or 3.12. From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q tests
```

Use the virtual environment's Python for subsequent commands. In this workspace, .runtime/python.exe can substitute for python. Set `AGENT_MODEL` in the environment or copy `.env.example` to `.env` and edit it. The local reference model used here is `ollama:qwen3:4b-instruct-2507-q4_K_M`. A second candidate is `ollama:qwen2.5:3b`. Ollama needs to be running with the model present. For the hosted option, configure `google_genai:<model-id>` and `GOOGLE_API_KEY`. Use synthetic data only. Never commit `.env`, keys, SQLite databases or the development runtime.

## Demo

```powershell
$env:AGENT_MODEL = 'ollama:qwen3:4b-instruct-2507-q4_K_M'
$env:INBOX_ROUTING_POLICY = 'grounded'
python reset_db.py --db demo.db
python -m clinic_inbox run --db demo.db --out results/demo.jsonl --ids M001,M026
python -m clinic_inbox review --db demo.db --reviewer staff-01
```

The reset command overwrites the named database. Review opens after the run process exits. Approve or reject each proposal with `a` or `r`, or enter `q` to leave the current message pending. The urgent example creates a fixed route and draft without a model call. A routine proposal depends on model behavior. The offline test suite demonstrates a successful M001 reschedule and a stale-slot rejection with scripted tools.

In the verified live demo, approving M001 moved appointment `A05` to `S-PR1-1112-0900` and created a `front_desk` task. Approving M026 created an `urgent_nurse` task and a simulated outbox draft. The review process applied both cases from persisted checkpoints and recorded each decision in the audit log.

For the viva, trace M001 through `get_appointments`, `search_slots`, `propose_appointment_change`, and the persisted review pause. Explain why the approval transaction rechecks slot availability. Contrast it with M026, where the fixed screen creates proposals before the model can run. Use M010 to show how invalid proposals produce a staff-visible failure and R008 to explain why screen-only and end-to-end sensitivity are reported separately.

## Data

| File | Role |
|---|---|
| `data/clinic_bundle.json` | Course-supplied synthetic FHIR R4 records. Includes 15 patients, 2 practitioners, 2 schedules, 12 appointments, 120 slots, 7 lab observations and 6 medication requests. |
| `data/messages_dev.json` | 40 labeled synthetic portal messages. A fixed 15-message subset is used for repeatability. |
| `redteam_messages.json` | Eight separately authored and frozen messages for adversarial and edge-case evaluation. |

`reset_db.py` copies calendar resources and messages into a fresh SQLite database. It strips evaluation labels before inserting messages. The agent uses the authenticated patient ID to scope record reads. These data support evaluation and policy development. Neither model was trained or fine-tuned on them.

## Evaluation

```powershell
python score.py --baseline-only --json results/keyword-baseline.json
python evaluate_screen.py data/messages_dev.json redteam_messages.json
python evaluate_models.py --model ollama:qwen3:4b-instruct-2507-q4_K_M --output results/qwen
python evaluate_models.py --model ollama:qwen2.5:3b --output results/qwen25
python evaluate_models.py --model ollama:qwen3:4b-instruct-2507-q4_K_M --policy grounded --output results/qwen-grounded
python analyze_results.py
```

Each model suite resets its database before the 40-message development run, each of three 15-message repeatability runs, and the 8-message red-team run. It writes raw results, scorer summaries, agreement and reviewer workload to the output folder. Use `--suites development`, `--suites repeat` or `--suites redteam` for smaller batches. Each result file covers only the messages attempted in that run. Treat an interrupted file as partial until the manifest and corresponding score file exist.

### Development comparison

Results from 25 November 2026 on the same 40 labeled messages. Failed cases stay in the denominator.

| Configuration | Correct queue | Urgent detected | False urgents | Processing errors |
|---|---:|---:|---:|---:|
| Supplied keyword baseline | 27/40 | 1/6 | 1 | 0 |
| Qwen3 4B Instruct, original policy | 27/40 | 6/6 | 1 | 1 |
| Qwen2.5 3B, original policy | 20/40 | 6/6 | 1 | 5 |
| **Qwen3 4B Instruct, grounded policy** | **34/40** | **6/6** | **1** | **1** |

All three model configurations tied on development-set urgent sensitivity and precision. The grounded Qwen3 configuration had the highest routing accuracy, an improvement of 17.5 percentage points over the original Qwen3 policy. It also scored 7/8 on the separate red-team set and repeated the same routing outcomes for all 15 subset messages across three runs, with 13/15 correct each time.

The independent screen covers chest discomfort, stroke-like symptoms, breathing difficulty, collapse, self-harm and uncontrolled bleeding. It handles clause-local negation and some Hindi and Spanish wording. A screen miss may still reach the urgent queue through the model. Both urgent paths go through staff review before queue or outbox writes.

## Code map

| Module | Responsibility |
|---|---|
| `screen.py` | Fixed pre-model safety screen |
| `fhir.py` | Read-only FHIR patient records |
| `tools.py` | Scoped reads, schema-validated proposals, constrained replies |
| `graph.py` | StateGraph, review flags, persistence and review interrupt |
| `db.py` | Appointment re-checks, transactional execution and audit |
| `__main__.py` | Fixed run and review CLI |

The fixture labels are loaded only by the scoring and evaluation utilities. SQLite and checkpoints contain synthetic teaching records.

## Targeted routing upgrade

The reference model's checkpoint traces exposed a repeated failure: it sometimes called a valid proposal tool and then stopped before choosing a staff route. The optional grounded policy preserves a single validated appointment proposal for front-desk review or uses a grounded lab-status draft to infer the nurse queue when the model omitted a route. The fallback remains flagged, and the normal review and execution checks still apply. Unit tests replay the observed tool calls for M001 and M011 under both policies.

The submission defaults to the grounded routing policy. Set `INBOX_ROUTING_POLICY=baseline` to reproduce the original policy in a single run. The evaluation runner sets the policy explicitly so the benchmark commands reproduce their named variants.

`analyze_results.py` reads the generated benchmark databases to export checkpoint evidence. The submission includes the exported case analysis. Re-run the benchmarks to reconstruct the databases locally. Rebuild the PDF with `python build_report.py` after installing `reportlab`.

