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


def test_normalization_risk_flags_erased_tokens():
    from claimidx.fingerprint import normalization_risk

    assert "path" in normalization_risk("failed at /home/runner/app/src/page.tsx")
    assert "url" in normalization_risk("see https://example.com/x")
    assert "int" in normalization_risk("status 503 from upstream")
    assert "str" in normalization_risk('Cannot read property "a long prose phrase here"')
    assert normalization_risk("ModuleNotFoundError: No module named 'pydantic_core'") == []


def test_fingerprint_stable_and_runtime_major():
    a = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@20.18.2", dep=["next@15.0.0"])
    b = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@20.11.1", dep=["next@15.0.0"])
    c = fingerprint(err="TypeError: params is a Promise", eco="npm", rt="node@18.20.0", dep=["next@15.0.0"])
    assert a == b and a != c and len(a) == 64
