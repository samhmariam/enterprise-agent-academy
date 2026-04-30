import json
import logging

from pydantic import ValidationError

from apps.meridian_core.schemas import ComplianceMemo

logger = logging.getLogger(__name__)


def parse_compliance_output(raw: str, correlation_id: str) -> ComplianceMemo | None:
    """Parse and validate LLM output. Returns None on failure; never raises."""
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")
        return ComplianceMemo(**data)
    except (json.JSONDecodeError, ValidationError, ValueError) as e:
        logger.error(json.dumps({
            "correlation_id": correlation_id,
            "event": "schema_validation_failure",
            "error": str(e),
            "raw_output_preview": raw[:200],
        }))
        return None  # Caller must handle None — do not silently continue
