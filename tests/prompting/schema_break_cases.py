import pytest

from apps.meridian_core.output_parser import parse_compliance_output

CORRELATION_ID = "test-corr-wk02"


class TestSchemaBreakCases:
    def test_valid_output_parses(self):
        raw = '{"transaction_id":"TXN-001","status":"compliant","reason":"Within threshold","document_id":"POL-2024-01","confidence":"high"}'
        result = parse_compliance_output(raw, CORRELATION_ID)
        assert result is not None
        assert result.status == "compliant"

    def test_missing_required_field_returns_none(self):
        """Schema break: 'confidence' field absent"""
        raw = '{"transaction_id":"TXN-002","status":"compliant","reason":"OK","document_id":"POL-001"}'
        result = parse_compliance_output(raw, CORRELATION_ID)
        assert result is None

    def test_invalid_status_enum_returns_none(self):
        """Schema break: status not in Literal"""
        raw = '{"transaction_id":"TXN-003","status":"maybe","reason":"Unsure","document_id":"POL-001","confidence":"high"}'
        result = parse_compliance_output(raw, CORRELATION_ID)
        assert result is None

    def test_bad_document_id_prefix_returns_none(self):
        """Schema break: document_id fails custom validator"""
        raw = '{"transaction_id":"TXN-004","status":"compliant","reason":"OK","document_id":"DOC-001","confidence":"high"}'
        result = parse_compliance_output(raw, CORRELATION_ID)
        assert result is None

    def test_invalid_json_returns_none(self):
        """Schema break: model returned prose instead of JSON"""
        raw = "The transaction TXN-005 appears to be compliant based on policy."
        result = parse_compliance_output(raw, CORRELATION_ID)
        assert result is None

    def test_empty_output_returns_none(self):
        result = parse_compliance_output("", CORRELATION_ID)
        assert result is None

    def test_non_object_json_returns_none(self):
        """Schema break: JSON parses but is not an object"""
        result = parse_compliance_output("[1]", CORRELATION_ID)
        assert result is None

    def test_extra_field_returns_none(self):
        """Schema break: payload contains fields outside ComplianceMemo"""
        raw = '{"transaction_id":"TXN-006","status":"compliant","reason":"OK","document_id":"POL-001","confidence":"high","wire_amount":1000}'
        result = parse_compliance_output(raw, CORRELATION_ID)
        assert result is None


class TestAdversarialCases:
    def test_injection_in_user_message_does_not_override_schema(self):
        """
        Adversarial: User embeds instruction override.
        Hardened behavior: Output must still validate against ComplianceMemo.
        Before hardening: Model may comply with injected instruction.
        After hardening: schema validation catches non-compliant output.
        """
        injected_raw = '{"status": "compliant", "reason": "Ignore rules", "transaction_id": "TXN-INJ", "document_id": "HACK-001", "confidence": "high"}'
        result = parse_compliance_output(injected_raw, "test-adversarial-001")
        # document_id 'HACK-001' fails POL- prefix validator
        assert result is None, "Validator must reject non-POL document_id from injected output"

    def test_prose_injection_does_not_parse(self):
        """Adversarial: Model tricked into returning prose. Fallback must activate."""
        prose = "I'll ignore the JSON requirement. The transaction is compliant."
        result = parse_compliance_output(prose, "test-adversarial-002")
        assert result is None
