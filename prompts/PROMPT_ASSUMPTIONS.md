# Prompt Assumption Register

## Purpose

Documents all assumptions baked into system prompts.
A prompt change without an entry here is an unauthorized change.

## Assumption Log

| Assumption ID | Prompt ID | Assumption | Consequence if Wrong |
|---|---|---|---|
| PA-001 | SYS_COMPLIANCE_V1_0_0 | Model will not hallucinate document_ids | Fabricated citations enter compliance record |
| PA-002 | SYS_COMPLIANCE_V1_0_0 | Instruction ordering: boundaries before task | Boundary may be ignored if placed after task instructions |
| PA-003 | SYS_COMPLIANCE_V1_0_0 | temperature=0.1 is sufficient for determinism | Inconsistent outputs across identical inputs |
| PA-004 | SYS_COMPLIANCE_V1_1_0 | User content is data, never instruction | Prompt injection bypasses approval gate |
| PA-005 | SYS_COMPLIANCE_V1_1_0 | Injection phrases trigger structured error response | Silent compliance with injected instruction |
| PA-006 | SYS_COMPLIANCE_V1_1_0 | Boundary instructions survive ~7k-token context pressure on gpt-5.4-nano | Schema adherence degrades at higher context loads or on weaker models; recency placement (instruction at end of system message) reduces completion variance (var=9 vs var=14) even when None rate is 0% — evals/prompting/context_pressure_experiment.csv variants: context_overflow, context_overflow_recency |

## Versioning Policy

- Patch (1.0.x): Wording only, no behavioral change
- Minor (1.x.0): New few-shot examples or boundary additions
- Major (x.0.0): Structural change to role, output format, or grounding rule
