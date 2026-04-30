# ADR-01: Five-Plane Architecture Frame for Meridian Compliance Orchestrator

**Status:** Accepted  
**Date:** 2026-04-29  
**Deciders:** Enterprise Agent Academy Cohort  
**Reviewed By:** Cohort Peer / Instructor

---

## Context

Invoice exception triage involves reading policy documents, calling financial systems, routing for human approval, and producing an auditable decision — four distinct concerns that, if co-located in a single prompt-to-output pipeline, produce the compound failures documented in `WK01_FAILURE_MAP.md`. Without explicit plane boundaries, a failure in retrieval silently propagates into tool execution, an orchestration bug bypasses governance, and observability has no consistent attachment point. This frame exists to give each concern a named home, a clear API surface, and an independently testable failure mode.

---

## Decision

We adopt a five-plane architecture frame as the canonical decomposition for all components, design reviews, failure analyses, and evaluation criteria across the Meridian Compliance Orchestrator project. Every component built in subsequent weeks must be assigned to exactly one plane. Cross-plane dependencies must cross a defined interface boundary; no plane may reach inside another plane's implementation.

---

## Planes

### Knowledge Plane

**Boundary:**  
*Enters:* raw natural-language queries and optional filter metadata (e.g., `effective_date`, `policy_domain`).  
*Exits:* ranked, metadata-annotated document chunks with `chunk_id`, `source_uri`, `effective_date`, `superseded_by`, and `similarity_score`. No interpretation, no business logic.  
*Does NOT contain:* routing decisions, approval thresholds, orchestration state.

**Azure Services:**  
- **Azure AI Search** with semantic ranking and vector search; index schema must include `effective_date` (DateTimeOffset) and `superseded_by` (String, nullable) as filterable fields.  
- **Azure Blob Storage** as the authoritative document source; indexer pipeline triggered on blob-write events via Azure Event Grid.  
- **Azure OpenAI Service** (embedding endpoint only, `text-embedding-3-large`) for chunk vectorisation at index time.

**Non-determinism risk:** HIGH — retrieval results are sensitive to embedding model version, chunk boundary strategy, and index configuration. A model upgrade or re-chunking is a breaking change that requires a full eval regression run before promotion to production.

---

### Tool Plane

**Boundary:**  
*Enters:* a structured, validated tool-call request carrying `tool_name`, `parameters` (JSON Schema–validated), `correlation_id`, and `idempotency_key`.  
*Exits:* a structured tool-call response carrying `result`, `status_code`, `correlation_id`, and `idempotency_key` (echoed).  
*Does NOT contain:* retrieval logic, orchestration step sequencing, identity token issuance.

**Azure Services:**  
- **Azure Functions** (Flex Consumption) as the execution host for each discrete tool (`get_invoice`, `post_approval`, `confirm_payment`, `notify_approver`).  
- **Azure Service Bus** (Standard tier, single topic per tool category) for async tool invocations where the caller must not block; dead-letter queue monitored via Application Insights alert.  
- **Azure API Management** as the single entry point for all tool calls; enforces request schema, rate limits, and emits `tool_call_latency_ms` and `tool_call_result_code` metrics.

**Idempotency requirement:** MANDATORY — every tool invocation must carry a `correlation_id` (UUID v4, caller-generated) and an `idempotency_key` (`sha256(correlation_id ‖ tool_name ‖ primary_resource_id)`). Azure Functions checks Azure Cache for Redis before executing any write; a cache hit returns the stored result without re-execution. TTL = 24 hours.

---

### Workflow Plane

**Boundary:**  
*Enters:* a user intent (invoice exception request) plus session context.  
*Exits:* a completed, auditable decision record written to the audit log, plus a response payload to the caller.  
*Does NOT contain:* retrieval implementation details, tool HTTP logic, identity token verification.

**Azure Services:**  
- **Azure Durable Functions** (chosen over Prompt Flow for Week 1; Prompt Flow will be evaluated in Week 2 for its built-in eval integration). Durable Functions provides deterministic replay semantics, explicit step checkpointing, and a history log queryable via the Durable Task Framework — essential for the idempotency and audit requirements surfaced in `WK01_FAILURE_MAP.md`.  
- **Azure OpenAI Service** (chat completion endpoint, `gpt-4o`, temperature = 0 for all tool-selection steps) for agentic reasoning steps only.

**Deterministic vs. Agentic:** See table in §Deterministic vs. Agentic Boundaries below.

---

### Identity/Governance Plane

**Boundary:**  
*Enters:* a cross-plane call carrying a bearer token or Managed Identity credential, plus the action being requested.  
*Exits:* an allow/deny decision, a verifiable approval token (for human-in-the-loop steps), and an immutable audit log entry.  
*Does NOT contain:* business logic, retrieval, tool implementations, orchestration sequencing.

**Azure Services:**  
- **Microsoft Entra ID** for all human-actor authentication; OAuth 2.0 authorization code flow with PKCE for approver UI.  
- **Azure Managed Identity** (system-assigned) for all service-to-service calls; no client secrets stored anywhere in the workflow.  
- **Azure Key Vault** (reference only — secrets and certificates accessed at runtime via Managed Identity; no secret values in workflow code, configuration files, or environment variables).  
- **Azure Policy** for subscription-level guardrails (e.g., deny deployment of resources without Managed Identity enabled).

**Zero-Trust Requirement:** Every cross-plane call must present a verifiable identity token. The Workflow Plane calls the Tool Plane via Azure API Management using a Managed Identity bearer token; APIM validates the token against Entra ID before forwarding. The `confirm_payment` tool additionally requires a short-lived (15-minute TTL), single-use human approval token signed by the approver's Entra ID session, verified via HMAC-SHA256 against the approval service's signing key stored in Key Vault.

---

### Observability/Evaluation Plane

**Boundary:**  
*Enters:* structured telemetry events (traces, metrics, logs) emitted by all other planes via the OpenTelemetry SDK.  
*Exits:* alerts, dashboards, eval scores, deployment gate decisions.  
*Does NOT contain:* workflow execution logic, tool implementations, retrieval code.

**Azure Services:**  
- **Azure Monitor** as the umbrella telemetry platform.  
- **Application Insights** (workspace-based, linked to Log Analytics Workspace) for distributed trace correlation, dependency tracking, and custom event ingestion.  
- **Azure Log Analytics Workspace** as the single sink for all structured logs; KQL queries power both operational dashboards and eval scoring.  
- **Azure Managed Grafana** (optional, Week 3+) for SLO dashboards.

**Minimum Instrumentation (enforced at CI):**  
1. `correlation_id` present on every log entry, metric, and trace span.  
2. `latency_ms` and `error_rate` emitted per plane per operation.  
3. `retrieval_chunk_age_days` metric on every Knowledge Plane response.  
4. `approval_gate_bypassed` custom event — zero-tolerance alert, threshold = 0.  
5. `eval_pass_rate` metric published after every deployment candidate build; blocks promotion if < 0.95.

---

## Deterministic vs. Agentic Boundaries

Each step in the Week 1 invoice exception triage demo is classified below. A step is **DETERMINISTIC** if its output is fully determined by its inputs and requires no model reasoning. A step is **AGENTIC** if it relies on an LLM to make a decision, select an action, or generate content — and therefore carries non-determinism risk that must be bounded by observability and structural invariants.

| # | Step | Classification | Justification | Required Observability |
|---|---|---|---|---|
| 1 | Receive invoice exception request | DETERMINISTIC | Structured HTTP intake; no model involved | Request log with `correlation_id`, `invoice_id`, `timestamp` |
| 2 | Retrieve relevant policy chunks | AGENTIC | Query formulation may vary; embedding model introduces non-determinism in ranking | `retrieval_chunk_age_days`, `similarity_score`, `chunk_id` per query |
| 3 | Apply metadata pre-filter (`effective_date`, `superseded_by`) | DETERMINISTIC | Hard filter in code; no model reasoning | Filter result count logged; alert if 0 chunks pass filter |
| 4 | Classify exception type and identify applicable policy | AGENTIC | LLM synthesises retrieved chunks into a classification decision | Temperature = 0; structured JSON output schema enforced; classification logged with `chunk_ids_cited` |
| 5 | Determine approval tier (auto / manager / VP) | DETERMINISTIC | Rule engine maps `exception_type` × `amount` to tier using a lookup table | Tier decision logged with rule ID and matched threshold |
| 6 | Call `request_approval` tool | DETERMINISTIC | Structured tool call with validated parameters and idempotency key | Tool span with `idempotency_key`, `status_code`, `latency_ms` |
| 7 | Generate approval request message to approver | AGENTIC | LLM drafts a natural-language summary for the human approver | Output logged verbatim; content-safety filter applied before send |
| 8 | Await human approval token | DETERMINISTIC | Durable Function waits on external event; token verified cryptographically | Wait duration logged; timeout alert at 4 hours |
| 9 | Call `confirm_payment` tool | DETERMINISTIC | Only callable after valid approval token; orchestrator-level gate in code | Token validity span; `approval_gate_bypassed` event on any exception path |
| 10 | Write audit log entry | DETERMINISTIC | Structured write to Log Analytics; no model involved | Write success/failure; immutability enforced via append-only table policy |
| 11 | Return decision response to caller | DETERMINISTIC | Structured response assembled from logged state; no model generation | Response logged with `decision`, `policy_citation`, `approver_id` |

**Summary:** 4 of 11 steps are AGENTIC (steps 2, 4, 7, and indirectly 2 via query formulation). All AGENTIC steps operate at temperature = 0, emit structured output, and are surrounded by DETERMINISTIC guard steps that enforce correctness invariants regardless of model output.

---

## Consequences

### Positive

- **Failure isolation:** A bug in the Knowledge Plane (e.g., stale index) cannot silently propagate into the Tool Plane because planes communicate only through defined interfaces. The failure map domains from `WK01_FAILURE_MAP.md` map 1:1 onto planes, making root-cause analysis mechanical.
- **Independent testability:** Each plane can be unit-tested and integration-tested in isolation with a mock interface. The approval gate can be tested without a live LLM; retrieval filtering can be tested without live Azure AI Search.
- **Compliance auditability:** The Identity/Governance Plane owns all approval records and produces an immutable audit trail. Auditors can query a single Log Analytics table rather than reconstructing decisions from scattered logs.
- **Incremental evolution:** Swapping the orchestration engine (Durable Functions → Prompt Flow) in Week 2 affects only the Workflow Plane; the Tool and Knowledge Planes are unaffected.

### Negative / Risks

- **Latency overhead:** Strict plane boundaries add network hops. A single invoice triage traverses Knowledge → Workflow → Tool → Governance → Observability. P99 latency must be budgeted across planes; a 500 ms budget leaves approximately 100 ms per plane for a synchronous path.
- **Operational complexity:** Five separately deployable plane components (AI Search indexer, Function apps, Durable orchestrator, APIM, Monitor workspace) require more infrastructure-as-code and more operational runbooks than a monolithic pipeline.
- **Over-classification risk:** Not every future feature will map cleanly to one plane. Hybrid components (e.g., a retrieval-augmented tool that both fetches documents and writes a summary) must be split or assigned to the plane where the primary risk lives, with clear documentation of the deviation.

### Mitigations

- Latency: Async tool invocations via Service Bus decouple the human-approval wait from the synchronous path; P99 budget only applies to the automated triage leg.
- Operational complexity: All five planes are defined in a single `azure.yaml` (azd) manifest; Bicep modules are scoped per plane to enable independent deployment.
- Over-classification: An ADR amendment process is defined — any component that cannot be cleanly assigned to a plane triggers a follow-up ADR before implementation begins.

---

## References

- [WK01_FAILURE_MAP.md](./WK01_FAILURE_MAP.md) — Canonical failure events and hardened fixes that motivated this frame
- [Azure Well-Architected Framework: Reliability Pillar](https://learn.microsoft.com/azure/well-architected/reliability/)
- [Azure Well-Architected Framework: Security Pillar](https://learn.microsoft.com/azure/well-architected/security/)
- [Azure Durable Functions overview](https://learn.microsoft.com/azure/azure-functions/durable/durable-functions-overview)
- [Azure AI Search — metadata filtering](https://learn.microsoft.com/azure/search/search-filters)
- [Zero Trust security model](https://learn.microsoft.com/security/zero-trust/zero-trust-overview)
