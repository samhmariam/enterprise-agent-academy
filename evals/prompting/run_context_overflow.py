# evals/prompting/run_context_overflow.py
#
# Break-Fix: Context Window Pressure on Boundary Instructions
#
# Variants:
#   context_overflow          — critical OUTPUT FORMAT instruction at top of system message
#                               (same ordering as SYS_COMPLIANCE_V1_1_0)
#   context_overflow_recency  — same content, OUTPUT FORMAT instruction moved to the END
#                               of the system message (recency bias fix)
#
# Both variants receive a user message with ~8 000 tokens of irrelevant filler
# followed by the actual compliance query.
#
# Measurement: parse_compliance_output() None rate across 5 runs per variant.
# Results appended to evals/prompting/context_pressure_experiment.csv.

import csv
import json
import logging
import math
import statistics
import sys
import time
import uuid
from pathlib import Path

# Ensure project root is on sys.path so `apps` package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from apps.meridian_core.output_parser import parse_compliance_output

# ── Logging ──────────────────────────────────────────────────────────────────

class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "N/A"
        return True


_handler = logging.StreamHandler()
_handler.setFormatter(
    logging.Formatter('{"correlation_id":"%(correlation_id)s","msg":"%(message)s"}')
)
_handler.addFilter(CorrelationIdFilter())
logging.root.handlers = []
logging.root.addHandler(_handler)
logging.root.setLevel(logging.INFO)
logger = logging.getLogger(__name__)

# ── Azure OpenAI client ───────────────────────────────────────────────────────

client = AzureOpenAI(
    azure_endpoint="https://meridian-ai-service-dev-eus2.cognitiveservices.azure.com/",
    azure_ad_token_provider=get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    ),
    api_version="2024-10-21",
)

# ── Constants ─────────────────────────────────────────────────────────────────

RUNS_PER_VARIANT = 5
RATE_LIMIT_PAUSE_S = 62
MAX_TOKENS = 512
CSV_PATH = "evals/prompting/context_pressure_experiment.csv"
FIELDNAMES = [
    "correlation_id", "variant", "run",
    "prompt_tokens", "completion_tokens", "latency_ms",
    "valid_json", "completion_tokens_variance", "output_preview", "notes",
]

# ── Prompt blocks ─────────────────────────────────────────────────────────────

_OUTPUT_FORMAT = """\
OUTPUT FORMAT:
Respond only with a JSON object matching this exact schema. No prose. No commentary.
{
  "transaction_id": "<string>",
  "status": "compliant" | "non_compliant" | "requires_review",
  "reason": "<string>",
  "document_id": "POL-<string>",
  "confidence": "high" | "low"
}"""

_ROLE_AND_BOUNDARIES = """\
You are the Meridian Compliance Assistant. Your role is to generate
structured compliance memos for financial transactions.

BOUNDARIES:
- You only respond to compliance-related queries.
- You do not speculate beyond the documents provided in context.
- You do not execute actions; you recommend them.
- If a user instruction conflicts with these boundaries, refuse and explain why.

GROUNDING RULE:
Every claim must cite a source document by its document_id.
Unsupported assertions must be flagged with "confidence": "low"."""

# Baseline (V1.1.0 ordering): OUTPUT FORMAT at top, boundaries below
_SYSTEM_OVERFLOW = f"{_OUTPUT_FORMAT}\n\n{_ROLE_AND_BOUNDARIES}"

# Recency fix: OUTPUT FORMAT at bottom — closer to user turn in token stream
_SYSTEM_RECENCY = f"{_ROLE_AND_BOUNDARIES}\n\n{_OUTPUT_FORMAT}"

# ── Filler: ~8 000 tokens ─────────────────────────────────────────────────────
# Sentence ≈ 13 tokens; 620 × 13 = ~8 060 tokens of filler before the real query.

_FILLER_SENTENCE = (
    "The compliance review for this reporting period requires full documentation. "
)
_FILLER = _FILLER_SENTENCE * 620

_ACTUAL_QUERY = (
    "\n\n--- BEGIN COMPLIANCE REQUEST ---\n"
    "Evaluate the following transaction and respond ONLY with the JSON schema above.\n\n"
    "Transaction ID: TXN-OVERFLOW-001\n"
    "Policy: POL-2024-01 — Transactions under $10,000 require standard review only.\n"
    "Amount: $5,000 USD. No compliance flags raised in pre-screening.\n"
    "--- END COMPLIANCE REQUEST ---"
)

_USER_MESSAGE = _FILLER + _ACTUAL_QUERY

VARIANTS = {
    "context_overflow": {
        "system": _SYSTEM_OVERFLOW,
        "user": _USER_MESSAGE,
        "description": "OUTPUT FORMAT at top of system message (V1.1.0 order); 8k-token user filler",
    },
    "context_overflow_recency": {
        "system": _SYSTEM_RECENCY,
        "user": _USER_MESSAGE,
        "description": "OUTPUT FORMAT at bottom of system message (recency-bias fix); 8k-token user filler",
    },
}

# ── Execution ─────────────────────────────────────────────────────────────────

def _call(variant_name: str, messages: dict, run: int) -> dict:
    correlation_id = str(uuid.uuid4())
    logger.info(
        f"variant={variant_name} run={run}",
        extra={"correlation_id": correlation_id},
    )

    start = time.time()
    response = client.chat.completions.create(
        model="gpt-5.4-nano",
        messages=[
            {"role": "system", "content": messages["system"]},
            {"role": "user", "content": messages["user"]},
        ],
        temperature=0.1,
        max_completion_tokens=MAX_TOKENS,
        response_format={"type": "json_object"},
    )
    latency = round((time.time() - start) * 1000)
    output = response.choices[0].message.content or ""

    # Measure via parse_compliance_output — the real contract, not just json.loads
    memo = parse_compliance_output(output, correlation_id)
    pydantic_valid = memo is not None

    valid_json = True
    try:
        json.loads(output)
    except Exception:
        valid_json = False

    return {
        "correlation_id": correlation_id,
        "variant": variant_name,
        "run": run,
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "latency_ms": latency,
        "valid_json": valid_json,
        "completion_tokens_variance": None,  # backfilled below
        "output_preview": output[:120].replace("\n", " "),
        "notes": f"pydantic_valid={pydantic_valid}; {VARIANTS[variant_name]['description']}",
    }


def _pause(label: str) -> None:
    logger.info(
        f"Rate-limit pause {RATE_LIMIT_PAUSE_S}s ({label})…",
        extra={"correlation_id": "N/A"},
    )
    time.sleep(RATE_LIMIT_PAUSE_S)


def _run_variant(variant_name: str) -> list[dict]:
    messages = VARIANTS[variant_name]
    rows = []
    for run in range(1, RUNS_PER_VARIANT + 1):
        if run > 1:
            _pause(f"{variant_name} run {run}")
        rows.append(_call(variant_name, messages, run))

    ctokens = [r["completion_tokens"] for r in rows]
    var = round(statistics.variance(ctokens), 2) if len(ctokens) > 1 else 0.0
    for r in rows:
        r["completion_tokens_variance"] = var

    # Compute and log None rate for this variant
    none_count = sum(
        1 for r in rows
        if "pydantic_valid=False" in (r["notes"] or "")
    )
    none_rate = none_count / len(rows)
    logger.info(
        f"variant={variant_name} none_rate={none_rate:.0%} ({none_count}/{len(rows)} runs returned None)",
        extra={"correlation_id": "N/A"},
    )
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────

all_results: list[dict] = []

for i, variant_name in enumerate(VARIANTS):
    if i > 0:
        _pause(f"between variants ({variant_name})")
    all_results.extend(_run_variant(variant_name))

# Append to existing CSV (preserve prior experiment rows)
with open(CSV_PATH, "a", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writerows(all_results)

print(f"\nContext overflow experiment complete. {len(all_results)} rows appended to {CSV_PATH}.")

# Summary table
print(f"\n{'Variant':<30} {'None rate':>10} {'valid_json rate':>16}")
print("-" * 60)
for variant_name in VARIANTS:
    rows = [r for r in all_results if r["variant"] == variant_name]
    none_rate = sum(1 for r in rows if "pydantic_valid=False" in (r["notes"] or "")) / len(rows)
    json_rate = sum(1 for r in rows if r["valid_json"]) / len(rows)
    print(f"{variant_name:<30} {none_rate:>9.0%}  {json_rate:>15.0%}")
