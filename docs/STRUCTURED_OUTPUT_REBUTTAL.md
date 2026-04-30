# Rebuttal: `response_format` + Pydantic vs. Regex Extraction

**Context:** A teammate proposed replacing `response_format={"type": "json_object"}` and Pydantic with
`re.search(r'\{.*\}', output, re.DOTALL)` to extract structured output from the Meridian compliance pipeline.

---

## The Rebuttal

`response_format={"type": "json_object"}` is a **token-level constraint**: the model's decoding
process is sampler-constrained so that every token emitted must maintain a valid partial JSON
structure. The model literally cannot produce a closing brace in the wrong position. Regex operates
on an already-emitted string; it imposes nothing on generation and can match anywhere — including
inside prose, error messages, or partial JSON nested inside a longer explanation. The model can still
return `{"status":"compliant"}` embedded in a sentence and the regex will extract it, silently
discarding the five required fields that are absent.

That silence is the critical failure. Pydantic raises a typed `ValidationError` that names the exact
field and constraint that failed. `parse_compliance_output` catches that error and emits a structured
`schema_validation_failure` log with a `correlation_id`. Every failure is a named, queryable event
in Log Analytics. A regex extractor that matched `{"status":"compliant"}` on a truncated response
would return a partial dict, `ComplianceMemo(**data)` would raise, but a regex-only pipeline has no
Pydantic step — the partial dict is used as if it were valid, and **the failure is invisible**.

In Meridian, an invisible failure means a compliance memo enters the record with a fabricated or
missing `document_id`. The `POL-` prefix validator exists precisely because hallucinated document IDs
(`HACK-001`, `DOC-42`) must never reach the audit trail — a regulator auditing that trail cannot
distinguish a fabricated citation from a real policy reference. If regex bypasses the validator, a
`HACK-001` document_id passes silently, the memo is persisted, and the audit trail is corrupted
without a single log line written.

**The correct answer:** `response_format={"type": "json_object"}` constrains generation.
Pydantic validates semantics. Both are required. Neither is optional in a compliance system.
Regex is string manipulation; it is not a contract.
