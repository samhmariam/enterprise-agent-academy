import logging
import json
import uuid
import sys
from azure.identity import DefaultAzureCredential
from azure.monitor.opentelemetry import configure_azure_monitor

# --- Structured Logging with Correlation ID ---
correlation_id = str(uuid.uuid4())
base_log_record_factory = logging.getLogRecordFactory()


def log_record_factory(*args, **kwargs) -> logging.LogRecord:
    record = base_log_record_factory(*args, **kwargs)
    record.correlation_id = correlation_id
    return record


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "timestamp": self.formatTime(record),
                "level": record.levelname,
                "correlation_id": record.correlation_id,
                "message": record.getMessage(),
            }
        )


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())

logging.setLogRecordFactory(log_record_factory)
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger(__name__)

# --- Azure Monitor Integration ---
# NEVER hardcode the connection string. Inject via environment variable:
#   export APPLICATIONINSIGHTS_CONNECTION_STRING="InstrumentationKey=..."
# Retrieve from Key Vault or Azure App Configuration in production.
configure_azure_monitor()


def verify_identity() -> bool:
    """Verify Managed Identity or developer credential is active. No secrets. No passwords."""
    logger.info("Starting identity verification")
    try:
        credential = DefaultAzureCredential()
        # Attempt to get a token for Azure Resource Manager as a liveness check
        token = credential.get_token("https://management.azure.com/.default")
        logger.info(
            "Identity verification PASSED | token_expiry=%s", token.expires_on
        )
        return True
    except Exception as exc:
        logger.error("Identity verification FAILED: %s", exc)
        return False


if __name__ == "__main__":
    logger.info("Platform check started | correlation_id=%s", correlation_id)
    result = verify_identity()
    logger.info(
        "Platform check complete | result=%s", "PASS" if result else "FAIL"
    )
    sys.exit(0 if result else 1)
