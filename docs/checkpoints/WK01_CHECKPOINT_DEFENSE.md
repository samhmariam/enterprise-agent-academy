# WK01 Checkpoint Defense — Lead Architect Review

**Author:** Samuel  
**Date:** 2026-04-30  
**Scope:** Spoken defense preparation for three high-stakes Week 1 review questions  
**Prerequisites:** `WK01_FAILURE_MAP.md`, `ARCHITECTURE_ADR_01.md`, `WK01_RED_TEAM_DELTA.md`

---

## How to Use This Document

Each section below is structured as a **spoken answer**, not bullet notes. Read it aloud. The goal is not memorisation — it is internalisation. If you cannot explain the reasoning in your own words without the document, the answer is not ready. The "What This Tests" framing from the original questions is retained alongside each answer because understanding what the examiner is probing is as important as the answer itself.

---

## Q1 — The Taxonomy Question

> *"You classified the duplicate action failure as an Orchestration failure. I would classify it as a Tool Misuse failure because the tool was called without a prior idempotency check. Defend your classification or revise it."*

**What This Tests:** Whether you understand that some failures span multiple planes, and whether you can defend a classification with a principled argument rather than capitulating under pressure. The correct response acknowledges the overlap and explains which plane owns the root cause vs. which plane surfaced the symptom.

---

### Spoken Answer

"You're not wrong, and I want to be precise about why both classifications are defensible before I tell you which one I'd anchor on.

Let me trace the failure chain. The orchestrator issued a `POST /approvals` call. It received a network timeout. It retried — without checking whether the first call had committed. The downstream API had no server-side deduplication — it created a new approval record on every successful `POST`. Two records appeared within 800 ms.

Now, where does the fault live?

**Your classification — Tool Misuse — is correct at the tool contract level.** The tool was called without a prior idempotency check. The Tool Plane's contract, as defined in ADR-01, explicitly requires every mutating call to carry a deterministic `idempotency_key` of the form `sha256(invoice_id ‖ action ‖ approver_id ‖ date)`, and the tool wrapper must check the Redis cache before issuing a downstream write. Neither of those controls existed. The tool was misused in the sense that it was invoked without satisfying the preconditions its contract demands. That is a Tool Plane failure.

**My classification — Orchestration — is correct at the failure trigger level.** The tool only got called twice because the orchestrator retried without state awareness. A correctly implemented Workflow Plane tracks which steps have committed. Azure Durable Functions, for instance, provides exactly this — deterministic replay semantics with step-level checkpointing. If the orchestrator had checked step history before re-dispatching the tool call, the second invocation would never have fired, regardless of whether the tool's idempotency key was present.

So here is my principled position: **the root cause lives in the Tool Plane** — the missing idempotency contract on the API is the vulnerability that made the duplicate consequential. **The trigger mechanism lives in the Orchestration Plane** — the retry without committed-state awareness is what activated that vulnerability. The failure required both links in the chain.

If I have to assign one plane, I assign it to **Tool Misuse**, and I revise my original classification. Here is why: if the tool had been idempotent, the orchestrator's retry would have been harmless — the second call would have returned the cached result and done nothing. The orchestrator's behaviour — retrying on timeout — is not itself incorrect; it is standard practice. The fault is that the tool the orchestrator was calling was not safe to retry. That is a Tool Plane contract violation.

The Orchestration failure is real and the hardened fix addresses it — exponential back-off, maximum two retries, same idempotency key reused on every retry — but it is a defence-in-depth addition, not the root cause remediation. The root cause remediation is the idempotency key and Redis cache check in the tool wrapper.

I should have recorded both planes in my failure map with an explicit note that the root cause was Tool Misuse and the trigger was Orchestration. In fact, looking at the failure domain field in `WK01_FAILURE_MAP.md`, I did write 'Tool Misuse / Orchestration' — but I didn't explain the ownership split. That is the gap. I own it."

---

### Examiner Follow-Up to Anticipate

**"How would you prevent this at design time, not just fix it after the fact?"**

"At design time, the ADR-01 Tool Plane contract is the mechanism. It states, explicitly, that every tool invocation must carry a `correlation_id` and an `idempotency_key` as mandatory fields on the tool-call request schema. That schema is enforced by Azure API Management before the call reaches the Function. If those fields are absent, APIM rejects the call with a 400 before any downstream state is touched. The constraint is structural — it cannot be skipped by the orchestrator or the model. Additionally, the pre-call guard in the hardened fix — `GET /approvals?invoice_id=...` before any `POST` — adds a second check at the tool boundary layer, independent of the Redis cache."

---

## Q2 — The Architecture Frame Question

> *"In ADR-01, you placed the approval gate in the Identity/Governance plane. Walk me through what happens — specifically, which services are involved and in what order — when a $75,000 transaction is submitted and the approval gate fires."*

**What This Tests:** Whether your ADR is a living technical document or a theoretical diagram. You must be able to narrate the exact Azure service call sequence — Entra ID token validation → policy check → approval record written to which store → audit log entry to which workspace.

---

### Spoken Answer

"I'll walk you through the full call sequence. There are eleven steps. I'll name the Azure service at each one.

**Step 1 — Intake.** The invoice exception request arrives at the Workflow Plane. Azure Durable Functions receives it via an HTTP trigger. The orchestrator logs the intake event: `invoice_id`, `amount` ($75,000), `correlation_id`, `timestamp` — written to Application Insights via OpenTelemetry.

**Step 2 — Policy retrieval.** The orchestrator calls the Knowledge Plane. The query goes to Azure AI Search with a mandatory metadata pre-filter: `effective_date <= today AND superseded_by IS NULL`. The relevant policy chunk — the one that defines the VP approval threshold — is returned with `chunk_id`, `similarity_score`, and `effective_date`. This is an AGENTIC step; the LLM running on Azure OpenAI (`gpt-4o`, temperature = 0) synthesises the chunk into a classification and tier determination.

**Step 3 — Tier determination.** The orchestrator applies the rule engine — this is a DETERMINISTIC step, no model involved. It maps `exception_type × amount` to an approval tier using a lookup table. $75,000 is above the Manager threshold and requires VP sign-off. The rule ID and matched threshold are logged.

**Step 4 — Approval gate fires.** The orchestrator calls the `request_approval` tool. This call exits the Workflow Plane and enters the Tool Plane. The call goes to **Azure API Management** first — that is the single entry point for all tool calls per ADR-01. The orchestrator presents a **Managed Identity bearer token** (system-assigned, `meridian-mi-dev-eus2`). APIM validates that token against **Microsoft Entra ID** before forwarding. If the token is invalid or the Managed Identity lacks the required role, APIM returns 401 and the orchestrator halts — the tool is never reached.

**Step 5 — Tool execution.** APIM forwards the validated request to the `request_approval` **Azure Function** (Flex Consumption). Before the Function executes any write, it checks **Azure Cache for Redis** for the `idempotency_key` — `sha256(invoice_id ‖ 'request_approval' ‖ approver_id ‖ date)`. On a first call, the key is absent; execution proceeds. The Function writes the approval request record — the store here is the ERP approval API, and the Function persists the pending approval state. The Redis cache is updated with the idempotency key and the result; TTL = 24 hours.

**Step 6 — Durable wait.** The Durable Functions orchestrator suspends and waits on an external event — the human approval token. Wait duration is logged. A timeout alert fires at 4 hours if no response arrives.

**Step 7 — Human approver authentication.** The VP approver opens the approval UI. The UI initiates an **OAuth 2.0 authorization code flow with PKCE** against **Microsoft Entra ID**. This is not a service principal login — it is a human-actor authentication, browser-based. The Entra ID session issues a short-lived, single-use approval token signed by the approver's session.

**Step 8 — Token delivery and verification.** The approval token is delivered back to the orchestrator as the Durable Functions external event. The orchestrator passes it to the approval service, which verifies it via **HMAC-SHA256** against the signing key. That signing key lives in **Azure Key Vault** and is accessed at runtime via the Managed Identity — no secret value is in any code or config file. If the HMAC check fails — wrong token, expired token, replayed token — the orchestrator raises `AuthorizationError` and the workflow halts. The approval gate is enforced in orchestrator code, not as a model instruction.

**Step 9 — Payment confirmation.** With a valid approval token in hand, the orchestrator calls `confirm_payment`. This call goes through the same APIM path as Step 4. The `confirm_payment` Azure Function has an additional pre-check: it inspects the calling context for `approval_token` and verifies it before making any downstream call. If `approval_token` is absent or invalid, the Function raises a hard exception — the tool does not execute. This is the control that stops prompt injection from bypassing the gate, because the check is in the tool code, not in the model's reasoning path.

**Step 10 — Audit log.** After `confirm_payment` succeeds, the orchestrator writes an immutable audit log entry to **Azure Log Analytics Workspace** — `meridian-law-dev-eus2`. The entry contains: `invoice_id`, `amount`, `decision`, `approver_id`, `approval_token_hash`, `policy_citation`, `correlation_id`, `timestamp`. This table is configured as append-only; no entry can be deleted or modified.

**Step 11 — Response.** The orchestrator assembles the structured response payload from logged state — no model generation at this step — and returns it to the original caller.

The full sequence is: Durable Functions → AI Search → OpenAI (reasoning) → APIM → Entra ID (MI token validation) → Azure Function (`request_approval`) → Redis (idempotency) → ERP API → Durable wait → Entra ID (human PKCE) → Key Vault (signing key) → Durable resume → APIM → Azure Function (`confirm_payment`) → Log Analytics (audit) → response.

That is eleven ordered steps across five planes and nine distinct Azure services."

---

### Examiner Follow-Up to Anticipate

**"What happens if the Durable Functions orchestrator restarts mid-wait — between Step 6 and Step 7 — and replays from the last checkpoint?"**

"Durable Functions replay is deterministic. When the orchestrator replays, it re-executes all steps up to the last committed checkpoint. Steps that have already completed — specifically, the `request_approval` tool call at Step 5 — will fire again during replay. This is exactly where the idempotency key in Redis protects us: the Function checks Redis, finds the key already present, and returns the cached result without re-writing to the ERP. The approval record is not duplicated. The wait at Step 6 re-registers for the external event; if the token has already arrived, Durable Functions delivers it immediately from the event history. If it has not arrived, the wait resumes. The replay is invisible to the downstream systems."

**"You said the approval record is written to 'the ERP approval API.' Where exactly is the approval pending state stored within Azure?"**

"That is a fair challenge. In the current ADR, the `request_approval` Azure Function persists the pending approval state to the downstream ERP via its API — the ERP is the system of record for approvals. Within Azure, the Durable Functions instance itself maintains the orchestration state in Azure Storage (Table Storage and Blob Storage, managed by the Durable Task Framework) — that is the checkpoint and history store for the orchestration steps, not the business approval record. If the ERP's approval API were replaced or unavailable, the Durable Functions history would still show that the tool was called, but the business record would be the ERP's responsibility. This is a cross-plane dependency documented in ADR-01 under the Tool Plane boundary."

---

## Q3 — The Observability Question

> *"Show me, right now, in your Log Analytics Workspace, all log entries for a single run of your platform check. Use a KQL query. How long did it take you to write that query?"*

**What This Tests:** Whether your structured logging with Correlation IDs actually works end-to-end — not just in local stdout. The expected KQL is provided and you must be able to reproduce it from memory under pressure.

---

### The KQL Query (Committed to Memory)

```kql
AppTraces
| where Properties.correlation_id == "<your-uuid>"
| project TimeGenerated, Message, SeverityLevel, Properties
| order by TimeGenerated asc
```

This query should take under 60 seconds to write. If it takes longer, the observability is broken — not the query.

---

### Spoken Answer

"I can write that query. Here it is:

```kql
AppTraces
| where Properties.correlation_id == '<correlation-id-from-the-run>'
| project TimeGenerated, Message, SeverityLevel, Properties
| order by TimeGenerated asc
```

I would take the correlation ID from the terminal output of the platform check run — every log line prints it explicitly because the logging format in `apps/platform_check/main.py` includes it as a structured field on every line.

Now — I need to be honest about what this query will and will not show, because the red team delta for Week 1 surfaced an important gap.

**What works today:** For a single-shot invocation of `apps/platform_check/main.py` — a script you run once, it exits — this query works correctly. The `correlation_id` is a UUID v4 generated at module import time. Every log line in that single execution carries the same UUID. The query returns all and only the log lines from that run. That is the scenario demonstrated on 2026-04-30, and the OTel telemetry was confirmed ingested (HTTP 200, 10 items accepted).

**What does not work at agent scale — and I own this:** The `correlation_id` is generated at `uuid.uuid4()` at module import time, not at request handler entry time. In any long-running process — an Azure Functions worker that handles multiple invocations, a Durable Functions replay loop, a Flask application — the UUID is generated once when the module loads and never refreshed. All subsequent requests handled by that process share the same UUID. A query filtering by that UUID would return every trace for the lifetime of the process, not a single run.

This is Attack Surface #4 in `WK01_RED_TEAM_DELTA.md`. The logging guardrail is only satisfied for single-shot script execution. The observability guarantee the architecture requires — 'filter all logs for a single run using one UUID' — is not yet met for the agent workloads this baseline is intended to support.

The fix — already tracked as a P2 remediation item for Week 2 — is to move correlation ID generation inside the request handler or function entrypoint:

```python
# Correct — request-scoped
def handle_request(invoice_id: str) -> dict:
    correlation_id = str(uuid.uuid4())
    # All log calls within this scope use this correlation_id
```

rather than:

```python
# Wrong — process-scoped (current implementation)
correlation_id = str(uuid.uuid4())  # Generated at module import

def handle_request(invoice_id: str) -> dict:
    # Uses the module-level correlation_id — same for all requests
```

A test that validates this fix: invoke the handler twice in the same process and assert that the two invocations produce distinct UUIDs in the log output. That test does not pass against the current code.

The query I wrote above is correct. The infrastructure it relies on — Application Insights, Log Analytics Workspace, OTel ingestion — is confirmed working. The gap is in the scope of the correlation ID, and I am not going to pretend that gap doesn't exist. It is documented, prioritised, and owned."

---

### Examiner Follow-Up to Anticipate

**"If the correlation ID is process-scoped and not request-scoped, how does your trace correlation guarantee hold at all?"**

"It holds only for the platform check baseline, which is a single-shot script. The guarantee is explicitly bounded in `WK01_RED_TEAM_DELTA.md` under the 'Controls Committed This Week' section: 'Structured log format per line: consistent for single-shot script execution.' It does not hold for multi-invocation processes. That boundary was stated honestly. The architecture's observability requirement — distributed trace correlation across multiple planes — requires W3C `traceparent` header propagation between the Workflow Plane and the Tool Plane, which is deferred to Week 2 when those planes exist. What is confirmed working today: OTel spans from a single process invocation flow to App Insights and are queryable in Log Analytics by `correlation_id`. That is the Week 1 baseline. It is a floor, not a ceiling."

**"What table in Log Analytics would you query for audit trail entries — not application traces, but the payment decision records?"**

"The immutable audit log entries written at Step 10 of the approval flow go to a custom table in the Log Analytics Workspace — the ADR specifies Log Analytics as the single sink for all structured logs. In the current implementation, the OTel traces flow into the `AppTraces` table via Application Insights. For the audit records, the target table would be a custom log table — let's call it `MeridianAuditLog_CL` — populated by the Durable Functions orchestrator via a direct Log Analytics ingestion API call or via a Diagnostic Setting from Application Insights custom events. The distinction matters: `AppTraces` is telemetry (operational visibility), the audit table is compliance (immutable decision records). They should be separate tables with separate retention policies. The audit table requires append-only access enforced via Azure Policy. I have the architecture for this documented in ADR-01; the implementation of the custom audit table is Week 2 scope."

---

## Defence Posture Summary

| Question | Core Principle | Where You Might Yield | Where You Must Not Yield |
|---|---|---|---|
| Q1 — Taxonomy | Root cause vs. symptom plane distinction | Accept that Tool Misuse is the better single-plane classification | Do not accept that the Orchestration plane is irrelevant — the retry behaviour is a real contributing factor |
| Q2 — Architecture Frame | Service-level specificity for every step | Acknowledge that the ERP approval state lives outside Azure (system of record question) | Do not yield on the Entra ID token validation before APIM forwards, the Redis idempotency check, the Key Vault signing key for approval token, and the append-only audit log in LAW |
| Q3 — Observability | Honest gap acknowledgement | Fully concede that the correlation ID is process-scoped, not request-scoped | Do not concede that the OTel pipeline doesn't work — it was confirmed working on 2026-04-30; the gap is scoping, not infrastructure |

---

## Self-Assessment Checklist

Before the review, confirm each item:

- [ ] Can recite the eleven-step approval flow from memory, naming the Azure service at each step
- [ ] Can write the KQL query in under 30 seconds without referring to this document
- [ ] Can explain the root-cause vs. symptom-plane distinction for the duplicate action failure without notes
- [ ] Have read through `WK01_RED_TEAM_DELTA.md` attack surfaces 1–9 and can speak to each one
- [ ] Can name all five planes and their ADR-01 boundary definitions from memory
- [ ] Can distinguish which steps in the approval flow are AGENTIC vs. DETERMINISTIC (from the ADR-01 table)
- [ ] Can explain why `confirm_payment` cannot be bypassed via prompt injection in the hardened architecture
