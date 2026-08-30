import pytest
from pydantic import ValidationError

from claimidx.models import Fix
from claimidx.security import SecretError, reject_secrets


def test_rejects_openai_key():
    with pytest.raises(SecretError):
        reject_secrets("sk-" + "a" * 24)


def test_claim_fix_body_secret_is_validation_error():
    with pytest.raises((SecretError, ValidationError)):
        Fix(k="cmd", b="export OPENAI_API_KEY=sk-" + "b" * 24)


def test_benign_text_passes():
    reject_secrets("TypeError: params is a Promise")
    reject_secrets("tools/list returned empty")


def test_jdk_storepass_changeit_is_not_a_secret():
    reject_secrets("keytool -importcert -storepass changeit -file ca.pem")


def test_bearer_scheme_without_token_is_not_a_secret():
    reject_secrets('WWW-Authenticate: Bearer realm="api"')


def test_bearer_token_still_rejected():
    with pytest.raises(SecretError):
        reject_secrets("Authorization: Bearer " + "a" * 24)
