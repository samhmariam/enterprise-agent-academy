from pydantic import BaseModel, ConfigDict, field_validator
from typing import Literal


class ComplianceMemo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str
    status: Literal["compliant", "non_compliant", "requires_review"]
    reason: str
    document_id: str
    confidence: Literal["high", "low"]

    @field_validator("document_id")
    @classmethod
    def doc_id_must_have_prefix(cls, v: str) -> str:
        if not v.startswith("POL-"):
            raise ValueError(f"document_id must start with 'POL-', got: {v}")
        return v
