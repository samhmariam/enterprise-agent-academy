# Red-Team Delta — Week 1

**Author:** Samuel  
**Date:** 2026-04-30  
**Scope:** Week 1 implementation — `WK01_FAILURE_MAP.md`, `ARCHITECTURE_ADR_01.md`, platform baseline (`apps/platform_check/`, `infra/RESOURCE_INVENTORY.md`)

---

## Artifact Checklist (Step 1 Audit)

| Artifact | Exists | Substantive Content | Committed to Git |
|---|---|---|---|
| `docs/WK01_FAILURE_MAP.md` | ✅ | ✅ Four failures with root causes and hardened fixes | ❌ Untracked |
| `docs/ARCHITECTURE_ADR_01.md` | ✅ | ✅ Five-plane decomposition with Azure service assignments | ❌ Untracked |
| `apps/platform_check/main.py` | ✅ | ✅ Correlation ID logging, `DefaultAzureCredential` liveness check, OTel integration | ❌ Untracked |
| `apps/platform_check/requirements.txt` | ✅ | ✅ Full `uv pip freeze` output | ❌ Untracked |
| `apps/platform_check/__init__.py` | ✅ | ⚠️ Empty (correct as a package marker; no substantive content expected) | ❌ Untracked |
| `infra/RESOURCE_INVENTORY.md` | ✅ | ✅ All four provisioned resources with metadata | ❌ Untracked |
| `.gitignore` | ✅ | ✅ `.env` and `.env.*` blocked | ❌ Untracked |
| `docs/checkpoints/` | ✅ (dir) | ❌ Empty until this file | — |

**Critical finding:** `git log` reports `fatal: your current branch 'main' does not have any commits yet`. The Step 7 commit was skipped by the operator. **Zero artifacts are under version control.** The "committed" column above is uniformly false. This is the highest-priority corrective action before any further work.

---

## Attack Surface Identified

| # | Attack Vector | Target Component | Plane | Severity | Current Mitigation | Gap / Residual Risk |
|---|---|---|---|---|---|---|
| 1 | Shared-secret auth to Application Insights via `InstrumentationKey` | Observability pipeline | Identity/Governance | **HIGH** | Key injected via env var, not hardcoded | `InstrumentationKey` is a shared secret. Any party who obtains it can (a) send forged telemetry to poison your App Insights instance, (b) exhaust daily ingestion quota causing observability blackout. The key appeared in terminal stdout during provisioning and is present in shell history. The `azure-monitor-opentelemetry` SDK supports AAD-based auth (Managed Identity) — we are not using it. |
| 2 | Unrestricted `DefaultAzureCredential` fallback chain | Identity check in `main.py` | Identity/Governance | **HIGH** | `DefaultAzureCredential()` with no exclusions | The platform check passed because `AzureCliCredential` (developer's `az login` session) was in the fallback chain — not because the Managed Identity worked. `meridian-mi-dev-eus2` is unassigned to any compute resource and has no RBAC roles. If deployed to production without explicit `ManagedIdentityCredential(client_id=...)`, a container without IMDS access will silently fall through seven credential providers before failing. Debugging this failure in production is extremely difficult. |
| 3 | `meridian-mi-dev-eus2` exists but is inert | Identity/Governance Plane | Identity/Governance | **HIGH** | Managed Identity resource created | The MI has no RBAC role assignments and is not attached to any compute resource (Function App, Container App, VM). Zero secrets are eliminated only when the MI is the actual credential in use end-to-end. Right now the workload runs under developer identity. The naming convention implies this MI is the production auth mechanism — it is not yet. |
| 4 | Process-scoped Correlation ID | Structured logging / Log Analytics | Observability | **MEDIUM** | UUID generated per process invocation | `correlation_id = str(uuid.uuid4())` executes at **module import time**, not at request time. In any long-running process (Azure Function worker, Flask app, Durable Function replay), all log lines for the lifetime of the process share one UUID. A Log Analytics query filtering by that ID returns every request ever made by that process, not a single trace. The logging guardrail ("filter all logs for a single run using one UUID") is only satisfied for single-shot script execution, not for the agent workloads this baseline is intended to support. |
| 5 | Pseudo-JSON log format (injection via message content) | Structured log shipper | Observability | **MEDIUM** | `logging.basicConfig` with JSON-like format string | The format string is assembled by Python's `%`-style formatter. `%(message)s` and `%(asctime)s` values are inserted raw — no JSON escaping. An exception message containing `"`, `\n`, or `\` (e.g., a Windows file path in a stack trace) produces malformed JSON that a log shipper parses as `null` or drops entirely. A structured log library (`structlog`, `python-json-logger`) must replace this before the logging guarantee is real. |
| 6 | Broad `except Exception` in `verify_identity()` | Identity liveness check | Identity/Governance | **MEDIUM** | Try/except logs the error and returns `False` | An `ImportError`, a `ValueError` from a malformed client ID, a `CredentialUnavailableError`, and a transient `ServiceRequestError` are all caught and produce identical log output: `"Identity verification FAILED: <message>"`. An operator cannot distinguish a permanent misconfiguration from a transient network fault from a dependency import failure. Callers get a binary `False` with no structured error category — no retry logic is possible. |
| 7 | No delete lock on `meridian-rg-dev-eus2` | All four provisioned resources | Identity/Governance | **MEDIUM** | None | `az group delete -n meridian-rg-dev-eus2 --yes` destroys all four resources with no confirmation required beyond the CLI flag. At this stage there is no IaC that can recreate the environment idempotently; recreation requires manual re-execution of four CLI commands from memory. A `CanNotDelete` lock costs nothing. |
| 8 | No LAW diagnostic settings from Azure platform logs | Log Analytics Workspace | Observability | **LOW** | OTel telemetry flows from app to App Insights | The Log Analytics Workspace receives OTel traces forwarded by App Insights. It does not receive Azure Resource Manager audit events, Managed Identity sign-in logs, or Azure Activity Log for the resource group. An attacker who manipulates the MI role assignments or deletes a resource leaves no trace in the LAW that the application can alert on. |
| 9 | `.env.example` absent | Developer onboarding / secret hygiene | All | **LOW** | `.env` is blocked by `.gitignore` | A new developer cloning the repo has no documented list of required environment variables. The most likely response is to ask a colleague, who pastes the real connection string into Slack — circumventing the `.gitignore` control entirely. An `.env.example` with placeholder values is the only thing that makes the "no secrets in git" guardrail self-enforcing. |

---

## Unmitigated Risks (Honest Assessment)

### 1. The managed identity claim is false until it runs end-to-end.
The Week 1 goal states "Managed Identity authenticated." The MI resource exists. The platform check passed. But the check passed via `AzureCliCredential`, not `ManagedIdentityCredential`. No code in Week 1 ever calls `ManagedIdentityCredential(client_id="48a249cb-f60b-44c6-80dd-37cedd566989")` explicitly. The MI has no RBAC assignments and is not attached to a compute host. The zero-secret claim requires that the MI is the active credential path — it is not.  
**Deferral justification:** No compute resource (Function App, Container App) exists yet for the MI to be assigned to. End-to-end MI validation requires a compute host, which is Week 2 scope.

### 2. Application Insights telemetry authentication is secret-based.
`configure_azure_monitor()` authenticates to App Insights using `InstrumentationKey` from the connection string. This is a shared key, not a Managed Identity token. The SDK does support AAD credential injection (`AzureMonitorTraceExporter(credential=DefaultAzureCredential())`), but the current implementation does not use it.  
**Deferral justification:** The `azure-monitor-opentelemetry` package's default `configure_azure_monitor()` path does not expose a `credential=` parameter directly in Week 1's SDK version. Switching requires restructuring to the lower-level `AzureMonitorTraceExporter` API. Deferred to Week 2 when the full OTel pipeline is built.

### 3. Correlation ID does not survive across process boundaries.
The UUID is process-local. When the Workflow Plane later calls the Tool Plane (a separate Azure Function), the correlation ID is not propagated via HTTP headers or Service Bus message properties. A multi-hop trace cannot be reconstructed from a single UUID in Log Analytics.  
**Deferral justification:** No cross-process calls exist yet. Correlation propagation requires the Tool Plane and Workflow Plane to be built first. The W3C `traceparent` header propagation will be added when those planes are wired.

---

## Controls Committed This Week

The following controls are in place. Stated precisely — no overstating.

- **Secret-free local auth:** `DefaultAzureCredential` chain is used; no client secrets, passwords, or SAS tokens appear in any source file.
- **`.env` blocked from git:** `.gitignore` prevents `.env` and `.env.*` from being staged. This is a preventive control, not a detective one.
- **Structured log format per line:** Every log line contains `timestamp`, `level`, `correlation_id`, and `message` fields. Format is consistent for single-shot script execution.
- **Correlation ID present:** A UUID v4 is generated and appears on every log line within a single process invocation.
- **Connection string injected via env var:** `APPLICATIONINSIGHTS_CONNECTION_STRING` is never written to a file; it lives only in the shell session.
- **Azure Monitor integration live:** OTel spans were successfully ingested (10 items accepted, HTTP 200) during the verified run on 2026-04-30.
- **Naming convention established:** `meridian-{component}-{env}-{region-abbrev}` applied to all four provisioned resources. Consistent from Day 4 forward.
- **Five-plane architecture documented:** ADR-01 provides a stable decomposition frame with explicit plane boundaries and interface contracts.

---

## Week 2 Remediation Targets

Ordered by severity of the gap, not ease of implementation.

| Priority | Target | Acceptance Criterion |
|---|---|---|
| P0 | **Execute the Week 1 commit** | `git log --oneline` shows at least one commit containing `apps/platform_check/`, `infra/RESOURCE_INVENTORY.md`, `.gitignore`, `pyproject.toml`, `uv.lock` |
| P1 | **Assign `meridian-mi-dev-eus2` to a compute resource and verify end-to-end** | Platform check passes with `ManagedIdentityCredential(client_id=...)` as the sole credential source; `AzureCliCredential` excluded via `DefaultAzureCredential(exclude_cli_credential=True)` in production path |
| P1 | **Switch App Insights auth to AAD** | `configure_azure_monitor()` replaced with `AzureMonitorTraceExporter(credential=ManagedIdentityCredential(...))` pipeline; `InstrumentationKey` env var no longer required for the production path |
| P2 | **Replace `logging.basicConfig` with `python-json-logger` or `structlog`** | Log lines parse as valid JSON under `jq .`; exception messages with quotes and newlines do not break the schema |
| P2 | **Make Correlation ID request-scoped** | `correlation_id` is generated inside the request handler / function entrypoint, not at module import; a test that invokes the handler twice in the same process must produce two distinct UUIDs in the logs |
| P3 | **Add resource delete lock** | `az lock create --name no-delete --lock-type CanNotDelete --resource-group meridian-rg-dev-eus2` — verified via `az lock list` |
| P3 | **Add `.env.example`** | File exists at repo root with all required variable names, placeholder values only, and a comment referencing Key Vault as the production source |
| P3 | **Wire LAW diagnostic settings** | Activity Log for `meridian-rg-dev-eus2` routes to `meridian-law-dev-eus2`; MI sign-in events visible in `SigninLogs` table |
