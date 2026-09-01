from claimidx.fingerprint import classify, fingerprint, normalize_error


def test_contraction_is_not_a_quoted_string():
    s = normalize_error("FAILED: Can't locate revision identified by '0002'")
    assert "Can't" in s or "Cant" in s
    assert s.startswith("FAILED:")
    assert "<STR>" not in s.split("locate")[0]


def test_quoted_strings_keep_identifiers():
    s = normalize_error('TypeError: Cannot read property "foo" of undefined')
    assert "foo" in s
    a = normalize_error("AttributeError: module 'pkgutil' has no attribute 'find_loader'")
    b = normalize_error("AttributeError: module 'numpy' has no attribute 'float_'")
    assert "pkgutil" in a and "find_loader" in a
    assert fingerprint(err=a, eco="py") != fingerprint(err=b, eco="py")
    prose = normalize_error('TypeError: Cannot read property "a long prose phrase here" of undefined')
    assert "<STR>" in prose


def test_paths_and_urls_and_numbers():
    s = normalize_error("failed at /home/runner/work/app/src/page.tsx line 14 see https://example.com/x")
    assert "<PATH>" in s and "<URL>" in s and "<N>" in s
    win = normalize_error(r"failed at C:\Users\runner\app\page.tsx")
    assert "<PATH>" in win
    mac = normalize_error("failed at /Users/runner/src/page.tsx")
    assert "<PATH>" in mac


def test_classify_async_and_module():
    assert classify("TypeError: params is a Promise") == "async_api"
    assert classify("ModuleNotFoundError: No module named 'x'") == "module_not_found"
    assert classify("npm ERR! ERESOLVE unable to resolve") == "lockfile_drift"


def test_missing_modules_keep_distinct_names():
    a = fingerprint(err="ModuleNotFoundError: No module named 'claude_code_sdk'", eco="py")
    b = fingerprint(err="ModuleNotFoundError: No module named 'autogen'", eco="py")
    assert a != b
    assert "claude_code_sdk" in normalize_error("ModuleNotFoundError: No module named 'claude_code_sdk'")


def test_tools_list_is_not_a_path():
    err = "Error: MCP error -32601: Method not found: tools/list"
    assert "tools/list" in normalize_error(err)
    assert "<PATH>" not in normalize_error(err)


def _placeholder_literals(fn) -> set[str]:
    import ast
    import inspect
    import re

    tree = ast.parse(inspect.getsource(fn))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"<[A-Z]+>", node.value):
            out.add(node.value)
    return out


def test_placeholder_vocabulary_is_closed():
    import re

    from claimidx.fingerprint import (
        PLACEHOLDERS,
        _PLACEHOLDER_RISK,
        _quote_token,
        normalize_error,
        normalization_risk,
    )

    assert PLACEHOLDERS == ("<STR>", "<URL>", "<PATH>", "<HEX>", "<N>")
    assert tuple(_PLACEHOLDER_RISK) == PLACEHOLDERS
    emitters = _placeholder_literals(normalize_error) | _placeholder_literals(_quote_token)
    assert emitters == set(PLACEHOLDERS)
    branches = {
        "<URL>": "see https://example.com/x",
        "<PATH>": "failed at /tmp/z",
        "<HEX>": "got deadbeef",
        "<N>": "retried 503 times",
        "<STR>": 'Cannot read property "a long prose phrase here"',
    }
    assert set(branches) == set(PLACEHOLDERS)
    for tok, sample in branches.items():
        assert tok in normalize_error(sample)
    raw = " ".join(branches.values())
    out = normalize_error(raw)
    emitted = set(re.findall(r"<[A-Z]+>", out))
    assert emitted == set(PLACEHOLDERS)
    flags = set(normalization_risk(out))
    for tok in emitted:
        assert _PLACEHOLDER_RISK[tok] in flags


def test_normalization_risk_flags_erased_tokens():
    from claimidx.fingerprint import normalization_risk

    assert "path" in normalization_risk("failed at /home/runner/app/src/page.tsx")
    assert "url" in normalization_risk("see https://example.com/x")
    assert "int" in normalization_risk("retried 503 times")
    assert "str" in normalization_risk('Cannot read property "a long prose phrase here"')
    assert normalization_risk("ModuleNotFoundError: No module named 'pydantic_core'") == []
    assert "str" in normalization_risk("Input should be thumbs_up input_value=<STR>")
    assert "url" in normalization_risk("see <URL>")
    assert "path" in normalization_risk("failed at <PATH>")
    assert "hex" in normalization_risk("got <HEX>")
    assert "int" in normalization_risk("status <N> from upstream")


def test_fingerprint_stable_and_runtime_major():
    a = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@20.18.2", dep=["next@15.0.0"])
    b = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@20.11.1", dep=["next@15.0.0"])
    c = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@18.20.0", dep=["next@15.0.0"])
    assert a == b and a != c and len(a) == 64
    py_a = fingerprint(err="ModuleNotFoundError: No module named 'x'", eco="py", rt="py@3.12")
    py_b = fingerprint(err="ModuleNotFoundError: No module named 'x'", eco="py", rt="py@3.9")
    assert py_a == py_b


def test_runtime_proof_key_keeps_python_minor():
    from claimidx.fingerprint import runtime_proof_key

    assert runtime_proof_key("py@3.12") == "py@3.12"
    assert runtime_proof_key("python@3.12.1") == "py@3.12"
    assert runtime_proof_key("py@3.9") != runtime_proof_key("py@3.12")
    assert runtime_proof_key("node@20.18.2") == "node@20"
    assert runtime_proof_key("node@18") != runtime_proof_key("node@20")
    assert runtime_proof_key("") == ""


def test_error_codes_survive_number_collapse():
    """ENOENT and EACCES are different failures with different fixes. Line numbers still collapse."""
    enoent = normalize_error("OSError: [Errno 2] No such file or directory: '/tmp/x/y.txt'")
    eacces = normalize_error("OSError: [Errno 13] Permission denied: '/tmp/x/y.txt'")
    assert "Errno 2" in enoent and "Errno 13" in eacces
    assert fingerprint(err=enoent, eco="py") != fingerprint(err=eacces, eco="py")
    win = normalize_error("OSError: [WinError 32] The process cannot access the file at line 40")
    assert "WinError 32" in win and "line <N>" in win
    http = normalize_error("openai.BadRequestError: Error code: 429 - rate limited after 3 tries")
    assert "Error code: 429" in http and "<N> tries" in http
    exit_code = normalize_error("##[error]Process completed with exit code 137.")
    assert "exit code 137" in exit_code
    status = normalize_error("Request failed with status code 503 (attempt 2)")
    assert "status code 503" in status and "attempt <N>" in status
    # Already-canonical rows recompute unchanged.
    canon = "OSError: [WinError <N>] The process cannot access the file"
    assert normalize_error(canon) == canon


def test_pre_normalized_err_is_flagged_at_ingest(tmp_path):
    from claimidx import ingest

    out = ingest(
        "AttributeError: <STR> object has no attribute <STR>",
        fix_k="patch",
        fix_b="x",
        eval='python -c "import sys"',
        eco="py",
        own="did:claimidx:test",
        db=tmp_path / "ix.sqlite",
    )
    assert "placeholder" in out["warn"]
