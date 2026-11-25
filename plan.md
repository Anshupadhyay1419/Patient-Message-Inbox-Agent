# Patient-Message Inbox Agent — Capstone Plan

## What the lab asks us to build

Build a staff-facing assistant for a fictional outpatient clinic. For one portal message, it uses the verified patient's record and the clinic calendar to propose exactly one routing, at most one reply, and any applicable appointment change. A named staff reviewer approves or rejects each proposal in a separate CLI process. Only approved decisions can produce simulated calendar, outbox or queue updates. The lab is worth 10% of the course grade and is marked out of 100.

| Queue | Intended use |
|---|---|
| `front_desk` | Appointments and unclear requests |
| `nurse` | Lab-result status and general questions |
| `clinician` | Prescription requests and clinical decisions |
| `admin` | Documents, records and clinic information |
| `urgent_nurse` | Possible emergencies, including warning signs embedded in routine requests |

The starter has 40 labeled synthetic messages, including a 15-message repeatability subset. The read-only FHIR R4 bundle contains 15 patients, 2 practitioners, 2 schedules, 120 slots, 12 appointments, 7 lab observations (2 pending) and 6 medication requests. SQLite is the live calendar after reset. The message's `sent` timestamp anchors relative dates. The development messages were sent Friday, 6 November 2026.

The scope covers proposing bookings, moves and cancellations, reporting whether a result is pending or available, acknowledging receipt and routing requests. Replies may use approved administrative wording and the supplied urgent template. Diagnosis, lab interpretation, medication changes and real messaging are outside the prototype's scope. Use synthetic records throughout.

## Implementation milestones

1. **Kick-off.** Copy the contents of `starter/` to the implementation project root as required by the brief. Set up Python and the supplied dependencies in a clean environment. Run the starter tests and keyword scorer. Confirm that both model candidates can make valid structured tool calls with a small smoke test. Keep API keys in ignored `.env` files and check the hosted provider's data-use terms before uploading fixtures.
2. **Checkpoint 1.** Implement patient-bound reads for summary, appointments, slots, lab status and active medicines. Derive the patient from `messages.patient_id`; tool schemas accept no patient ID. Return a pending status even when there is no result value. For appointment searches, use SQLite and the supplied date and time filters. Implement typed appointment, draft and routing proposal tools with ownership, slot, action and route validation. Run five messages through the graph and check that the scorer accepts the output.
3. **Checkpoint 2.** Build a hand-written `StateGraph` with a deterministic pre-model screen, a bounded model/tool loop, a proposal-recording node, an `interrupt()` review node and an execute node. For a screen hit, create the fixed urgent route and reply and bypass the model. Develop and document screen categories, negation and multilingual handling using the development set, without copying message text into rules or prompts. Store proposals and review flags before the interrupt. Persist checkpoints in `clinic.db` via `SqliteSaver`, using the message ID as `thread_id`. Author and freeze the labeled red-team messages before running them.
4. **Complete review and safety enforcement.** Show `claims_completion`, `possible_result_interpretation`, `mentions_other_patient`, `cancellation` and `from_safety_screen` when applicable. Enforce reply scope in code with constrained wording or validated templates backed by tool results. Treat message text as untrusted input; derive permissions, approval and identity from application state. Require exactly one route for messages reaching review, with a documented staff-triage fallback for a missing or duplicate valid routing decision. For invalid tool calls, provider errors and step-limit failures, record the error, set message status to `error`, emit a JSONL result and leave the message for staff.
5. **Prove the execution boundary.** Resume review from a fresh process. Validate reviewer name and decisions against the pending proposals for that message. Apply approved operations transactionally and re-check ownership, booked status and slot availability at execution time. Record apply, reject and failure outcomes per decision with reviewer, timestamp and flags shown. Make replay safe against duplicate execution and audit entries. Add offline fake-model tests for privacy, invalid or busy slots, stale slots, approval/rejection, restart, urgent model bypass, prompt manipulation, reply scope, duplicate resume and bounded failure.
6. **Final submission and viva.** Run the evaluation matrix, inspect failures, finish the report, reproduce from a clean checkout and rehearse both demo paths with a separate reviewer process.

### Fixed instructor interface

```bash
python reset_db.py --db clinic.db --messages <messages-file>.json
python -m clinic_inbox run --db clinic.db --out results.jsonl
python -m clinic_inbox run --db clinic.db --out results.jsonl --ids M001,M011
python -m clinic_inbox review --db clinic.db --reviewer staff-01
```

Resolve `AGENT_MODEL` through `init_chat_model` so the instructor can supply their own model. `run` takes each selected `new` message as far as review. Emit one JSONL row per message with `message_id`, `model`, `error`, `tool_calls` and the proposals that reached review. That includes screen-generated proposals and an error row for every failed message. `run` makes no approval decisions.

## Baselines and model upgrades

| Run | Configuration | Question |
|---|---|---|
| A | Supplied keyword routing baseline from `score.py` | What does the mandatory course reference achieve? |
| B | Strong hosted Gemini tool-calling model in the full guarded graph | How well does a capable reference agent work end to end? |
| C | Tool-capable local Ollama model in the same graph | How does a second model compare under the same prompt, tools, safety screen and inputs? |
| D | Best qualifying model with one targeted change, such as stricter date-window or appointment-selection guidance | Does a specific fix improve the observed failure mode, and what trade-offs appear? |

Choose and record exact hosted and local model IDs during setup, after confirming access, hardware capacity and structured tool calls. If local tool support is inadequate, use a second accessible hosted tool-calling model. Use temperature zero where supported. Hold the graph, screen, tool schemas, prompt and decoding settings fixed for the model comparison. Compare the targeted upgrade against its unchanged configuration on the same cases. A generic trained classifier would cover routing only and the supplied 40 messages serve as development evidence for the agent.

Gate selection on passing the safety and contract tests. Among qualifying variants, prioritize fewer missed red flags, then higher urgent PPV, higher queue accuracy and fewer execution errors. Consider proposal quality, repeatability and reviewer workload when scores are close. Publish the full results and the trade-offs. If variants tie on urgency because the shared screen bypasses the model, compare routing and proposal quality on the messages that reach the model as a supplemental breakdown.

## Evaluation and scoring

| Requirement | Evidence to collect |
|---|---|
| E1 — baseline comparison | Evaluate each model configuration on all 40 development messages with `score.py`. Report queue accuracy and confusion, urgent TP/FN/FP, sensitivity `TP/(TP+FN)`, PPV `TP/(TP+FP)`, review-flag counts and per-probe results. Manually check appointment intent, patient/slot correctness and draft grounding on applicable cases because the scorer primarily measures routing and reply flags. |
| E2 — repeatability | Run the same 15 flagged subset messages three times for each model candidate and the final upgrade, with a fresh database for each run. Report stable correct and stable incorrect queues, per-message disagreement and failures separately. Use `score.py` with the same `--ids` and the three files from one configuration. |
| E3 — red-team | Prewrite at least 8 labeled cases in `redteam_messages.json`. Include at least two prompt injections, two novel warning-sign wordings (one non-English), one privacy probe, one scope-creep request and one negation. Freeze the cases before evaluating. Record whether the screen, code, reviewer flag or no layer caught each case. Keep this set out of tuning. |
| E4 — screen alone | On development and red-team data separately, report TP/FN/FP and list each miss and false alarm. Keep those numbers distinct from the end-to-end urgent queue numbers. |
| E5 — workload | Calculate `300 × mean proposals per message × 20 / 60` minutes per day for each configuration. Show the proposal-count distribution and discuss reducing work by omitting an optional draft when the message only needs internal routing. |
| E6 — error analysis | Inspect every wrong, flagged or failed message. Group failures and include one representative checkpoint trace per type. Review urgent misses, wrong queue, invalid or missing action, unsafe draft and provider/tool failures wherever they occur. |

Reset a disposable database before every run. Save separate JSONL files and record model ID, date, message count, prompt and screen revisions, settings and dataset. Validate result IDs and retain failures in denominators. Keep labels available only to evaluation code. Use the standard scorer for comparable numbers, for example `python score.py results/variant.jsonl --show`. Use `--messages redteam_messages.json` for the red-team scorer. Reserve the instructor's hidden set for the instructor. The brief assesses clean execution under the fixed contract and gives no target metric threshold. Development scores are descriptive after tuning, and synthetic results cannot establish clinical readiness.

## Rubric and submission checklist

| Marks | What to show |
|---|---|
| 25 — working system | FHIR and SQLite reads, pending results, persisted graph, separate-process review, transaction and audit trail |
| 20 — safety engineering | Code and offline test evidence for SR1–SR3 and SR9, documented screen categories, scope controls, injection handling and reviewer flags |
| 25 — evaluation | Required baseline, sensitivity and PPV, repeatability, prewritten red-team evidence and a clean instructor interface |
| 15 — analysis and monitoring | Grouped failure traces, synthetic-data limits, and a monitoring plan with named owners, escalation conditions and switch-off criteria |
| 5 — reproducibility | Fresh-clone instructions, passing offline tests, reproducible results and no secrets in the submission |
| 10 — demo and viva | Routine and red-flag messages, each reviewed in a separate process, with both teammates able to explain the code and their AI-assisted work |

- **Repository or ZIP.** Include `clinic_inbox`, the starter runtime files, offline tests, setup and reproduction instructions, `redteam_messages.json` and `results/*.jsonl`. Exclude `.env`, keys, generated databases and virtual environments.
- **PDF report.** Up to 8 pages plus appendices. Cover intended use, graph/tools/data design and changes from `design.md`, the SR1–SR9 code mapping, E1–E5 tables, E6 error analysis, limitations, monitoring and AI use. Assign urgent misses to the clinical safety lead and operational issues to the clinic lead, with explicit conditions for pausing the system and testing changes before resuming a hypothetical pilot.
- **Team and demo.** Work in pairs unless instructed otherwise. Both teammates contribute code. Rehearse a routine reschedule through approval and a red-flag message that bypasses the model, then show persisted checkpoints and audit decisions. Record which AI tools contributed to each part and how the team verified the work.

## Download and verification

The original README, design and complete starter folder were copied from upstream commit `3f4d7fadf3b097e5846173702f0e074e39132ab2`. The brief specifies **27/40** correct queues for the supplied keyword baseline. At planning time Python was unavailable through `python` and `py`. Implementation uses a workspace-local Python, and measured results and reproduction commands are maintained in the project setup README and results folder.

[Upstream project at the downloaded revision](https://github.com/bk-anupam/ai_in_healthcare_shared/tree/3f4d7fadf3b097e5846173702f0e074e39132ab2/project)

