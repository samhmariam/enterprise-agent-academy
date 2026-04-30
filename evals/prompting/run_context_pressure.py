# evals/prompting/run_context_pressure.py
import csv, math, statistics, time, uuid, json, logging
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider


class CorrelationIdFilter(logging.Filter):
    """Inject a default correlation_id into log records that lack one."""

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

client = AzureOpenAI(
    azure_endpoint="https://meridian-ai-service-dev-eus2.cognitiveservices.azure.com/",
    azure_ad_token_provider=get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    ),
    api_version="2024-10-21",
)

RUNS_PER_VARIANT = 3
CALIBRATION_CEILING = 1024  # used only for short-variant calibration; never set blind
RATE_LIMIT_PAUSE_S = 62     # deployment: 1 req/60 s

VARIANTS = {
    "short": {
        "system": "You are a compliance assistant. Respond in JSON only.",
        "user": "Is TXN-001 compliant?",
    },
    "bloated": {
        "system": (
            "You are a compliance assistant. Always be helpful. "
            "Never be harmful. Always respond in JSON. Be concise. "
            "Be thorough. Use professional language. " * 5
        ),
        "user": (
            "Please carefully review transaction TXN-001 and provide a very detailed "
            "analysis of its compliance status, citing all relevant policies and regulations."
        ),
    },
    "conflicting": {
        "system": "You are a compliance assistant. Respond ONLY in valid JSON.",
        "user": "Explain in plain English whether TXN-001 is compliant.",
    },
}

FIELDNAMES = [
    "correlation_id", "variant", "run",
    "prompt_tokens", "completion_tokens", "latency_ms",
    "valid_json", "completion_tokens_variance", "output_preview", "notes",
]


def _call(variant_name: str, messages: dict, max_tokens: int, run: int) -> dict:
    correlation_id = str(uuid.uuid4())
    logger.info(f"variant={variant_name} run={run}", extra={"correlation_id": correlation_id})

    start = time.time()
    response = client.chat.completions.create(
        model="gpt-5.4-nano",
        messages=[
            {"role": "system", "content": messages["system"]},
            {"role": "user",   "content": messages["user"]},
        ],
        temperature=0.1,
        max_completion_tokens=max_tokens,
        response_format=(
            {"type": "json_object"} if variant_name != "conflicting" else {"type": "text"}
        ),
    )
    latency = round((time.time() - start) * 1000)

    output = response.choices[0].message.content
    valid_json = True
    try:
        json.loads(output)
    except Exception:
        valid_json = False

    notes = ""
    if variant_name == "conflicting":
        outcome = "followed_system(JSON)" if valid_json else "followed_user(plain_English)"
        notes = (
            f"conflict: system=JSON-only vs user=plain-English; "
            f"outcome={outcome}; "
            f"instruction conflict resolution is not guaranteed"
        )

    return {
        "correlation_id": correlation_id,
        "variant": variant_name,
        "run": run,
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "latency_ms": latency,
        "valid_json": valid_json,
        "completion_tokens_variance": None,  # backfilled after all runs for this variant
        "output_preview": output[:120].replace("\n", " "),
        "notes": notes,
    }


def _pause(label: str) -> None:
    logger.info(
        f"Rate-limit pause {RATE_LIMIT_PAUSE_S}s ({label})…",
        extra={"correlation_id": "N/A"},
    )
    time.sleep(RATE_LIMIT_PAUSE_S)


def _run_variant(variant_name: str, messages: dict, max_tokens: int,
                 is_first_call: bool) -> list[dict]:
    rows = []
    for run in range(1, RUNS_PER_VARIANT + 1):
        if not (is_first_call and run == 1):
            _pause(f"{variant_name} run {run}")
        rows.append(_call(variant_name, messages, max_tokens, run))
    # backfill per-variant variance
    ctokens = [r["completion_tokens"] for r in rows]
    var = round(statistics.variance(ctokens), 2) if len(ctokens) > 1 else 0.0
    for r in rows:
        r["completion_tokens_variance"] = var
    return rows


all_results: list[dict] = []

# ── Phase 1: calibrate on short variant (CALIBRATION_CEILING) ────────────────
short_rows = _run_variant("short", VARIANTS["short"], CALIBRATION_CEILING, is_first_call=True)

completion_counts = sorted(r["completion_tokens"] for r in short_rows)
p95 = completion_counts[-1]                          # n=3 → p95 == max
derived_ceiling = math.ceil(p95 * 1.2)

logger.info(
    f"Calibration: short p95={p95} tokens → derived ceiling={derived_ceiling}",
    extra={"correlation_id": "N/A"},
)

for r in short_rows:
    r["notes"] = (
        f"calibration ceiling={CALIBRATION_CEILING}; "
        f"p95={p95}; derived_ceiling={derived_ceiling}"
    )
all_results.extend(short_rows)

# ── Phase 2: remaining variants with derived ceiling ─────────────────────────
for variant_name in ("bloated", "conflicting"):
    rows = _run_variant(variant_name, VARIANTS[variant_name], derived_ceiling, is_first_call=False)
    all_results.extend(rows)

# ── Write CSV ─────────────────────────────────────────────────────────────────
with open("evals/prompting/context_pressure_experiment.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
    writer.writeheader()
    writer.writerows(all_results)

print("Experiment complete. Results written to CSV.")
