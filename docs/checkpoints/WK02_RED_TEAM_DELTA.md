# Red-Team Delta — Week 2

**Author:** Meridian Team | **Date:** 2026-04-30
**Scope:** Prompt versioning, context pressure, schema contracts, adversarial hardening

## New Attack Surface Introduced This Week

| Vector | Plane | Severity | Mitigation Status |
|---|---|---|---|
| Prompt injection via user message | Workflow / Knowledge boundary | HIGH | Partially mitigated (prompt boundary); Azure AI Content Safety not yet wired |
| Context overflow degrades boundary adherence | Workflow Plane | MEDIUM | Documented in experiment; no hard token ceiling enforced in production path |
| Schema contract drift (Pydantic model vs. prompt) | Workflow / Observability boundary | MEDIUM | Pydantic validates; no alert if None rate exceeds threshold |

## Controls Committed This Week

- Versioned prompt register with assumption log
- Pydantic schema validation with structured failure logging
- Adversarial test cases in CI coverage

## Unmitigated Risks

1. Azure AI Content Safety not yet integrated — prompt-level boundary only
2. No alerting on `schema_validation_failure` event rate in Application Insights
3. Prompt version in production not pinned via deployment config — manual discipline only

## Week 3 Remediation Targets

- Wire Azure AI Content Safety to all inbound user messages
- Add Log Analytics alert rule on `schema_validation_failure` event count > 0 in 5 min window
- Pin prompt version via Azure App Configuration feature flag
