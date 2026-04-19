# Anti-pattern: JWT / auth-token leakage in logs, alerts, CLI output

Context: RISK-3 from `.claude/teams/feature-eimzo-reliability/DECISIONS.md`.
The SessionEnd hook persists the last 15 bash commands to disk — any `curl -H "Authorization: Bearer <jwt>"` lands there **permanently**.
Healthcheck `--json` output ends up in cron logs. TG alerts are world-readable inside the group.

## Forbidden

```python
# BAD: dumping full response
logger.info("resp: %s", resp.json())
print(resp.text)
logger.debug("headers: %s", resp.headers)
logger.info("request headers: %s", request.headers)

# BAD: stringifying a dict that may contain the token
logger.info("payload: %s", {"token": jwt, "user": uid})

# BAD: token in error message
raise RuntimeError(f"Auth failed with token {jwt[:50]}...")

# BAD: token in TG alert body
notifier.send(f"Login failed — token was {jwt}")

# BAD: keys in --json healthcheck output
results = {"my_platform": {"token": jwt, "exp": ...}}
print(json.dumps(results))
```

## Required

```python
# GOOD: platform + len + alg + exp only
logger.info("Token for %s (len=%d, alg=%s, exp=%s)", platform, len(tok), alg, exp)

# GOOD: validated JWT, reveal only alg + which key matched
logger.info("Using localStorage key %r for %s (JWT validated, alg=%s)", key, platform, alg)

# GOOD: error without secret
raise TokenExtractionError(f"{platform}: no JWT-valid candidate among {list(PLATFORMS[platform]['token_keys'])}")

# GOOD: TG alert — platform name only
notifier.send(f"❌ {platform} JWT refresh failed 3 times")
```

## Feature DoD gate (before merge)

```bash
git grep -nE "(logger|print).*(resp\.json|response\.json|\.headers|token[^_])" \
    crawler/auth crawler/adapters/api.py \
    crawler/scripts/healthcheck.py crawler/scripts/mac_eimzo_daemon.py
```

Every match must be reviewer-blessed. Allowed hits are comments and the
sanitized forms above.

## Unit-test guard (task #7)

```python
def test_healthcheck_json_has_no_tokens():
    hc = run_healthcheck()
    blob = json.dumps(hc.results).lower()
    for forbidden in ("eyj", "pkcs7", "signature", "jwt", "bearer"):
        assert forbidden not in blob, f"{forbidden!r} leaked into --json output"
    # `auth_token:<platform>` storage key PREFIX is metadata (not a secret) and allowed.
```

## If you need to debug a token issue

Drop into a local ipython/REPL with env vars — don't commit log lines that
would emit the token if `LOG_LEVEL=DEBUG` is set in prod.
