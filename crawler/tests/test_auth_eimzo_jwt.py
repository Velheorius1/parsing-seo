"""Tests for crawler.auth_eimzo — RISK-3 (token hygiene) + RISK-5 (JWT validation).

Scope limited to PURE helpers so no USB token / CAPIWS / Supabase is needed:
- ``_is_jwt`` — rejects UUIDs, refresh tokens, HS256-none tricks.
- ``_candidate_keys`` — schema back-compat (single token_key vs multi token_keys).
- Log-leakage static grep (RISK-3 DoD gate).
"""

import base64
import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest

from crawler.auth_eimzo import _candidate_keys, _is_jwt


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_jwt(header, payload=None, signature="abc"):
    """Build a minimally-structured JWT (unsigned body; signature is a stub)."""
    def _enc(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return "%s.%s.%s" % (_enc(header), _enc(payload or {}), signature)


# ─── _is_jwt ────────────────────────────────────────────────────────────────

class TestIsJwt:
    def test_valid_rs256(self):
        assert _is_jwt(_make_jwt({"alg": "RS256", "typ": "JWT"}))

    def test_valid_hs256(self):
        assert _is_jwt(_make_jwt({"alg": "HS256", "typ": "JWT"}))

    def test_uuid_rejected(self):
        # UUIDs are the canonical false-positive _is_jwt must reject (RISK-5).
        assert not _is_jwt(str(uuid.uuid4()))

    def test_long_opaque_token_rejected(self):
        # 40-char random string — passes the old len>20 heuristic, not the new check.
        assert not _is_jwt("x" * 40)

    def test_alg_none_rejected(self):
        # Unsigned "none" tokens must not be accepted even if structure parses.
        assert not _is_jwt(_make_jwt({"alg": "none", "typ": "JWT"}))

    def test_missing_alg_rejected(self):
        assert not _is_jwt(_make_jwt({"typ": "JWT"}))

    def test_missing_typ_rejected(self):
        assert not _is_jwt(_make_jwt({"alg": "RS256"}))

    def test_empty_string_rejected(self):
        assert not _is_jwt("")

    def test_two_parts_rejected(self):
        assert not _is_jwt("header.payload")

    def test_garbage_header_rejected(self):
        assert not _is_jwt("nothex.nothex.sig")


# ─── _candidate_keys ────────────────────────────────────────────────────────

class TestCandidateKeys:
    def test_multi_preferred(self):
        p = {"token_keys": ["a", "b"], "token_key": "legacy"}
        # token_keys takes precedence — never fall back to the single alias.
        assert _candidate_keys(p) == ["a", "b"]

    def test_single_legacy(self):
        assert _candidate_keys({"token_key": "access_token"}) == ["access_token"]

    def test_empty(self):
        assert _candidate_keys({}) == []

    def test_empty_list_falls_back_to_single(self):
        # token_keys: [] should fall through to token_key, not explode.
        p = {"token_keys": [], "token_key": "fallback"}
        assert _candidate_keys(p) == ["fallback"]

    def test_returns_copy(self):
        p = {"token_keys": ["a"]}
        got = _candidate_keys(p)
        got.append("mutated")
        assert p["token_keys"] == ["a"], "caller must not mutate the source list"


# ─── RISK-3 static DoD gate ─────────────────────────────────────────────────

class TestNoTokenLeakage:
    """Fail CI if a future edit logs/prints response bodies or headers near auth code."""

    FILES = [
        "crawler/auth_eimzo.py",
        "crawler/auth/session_store.py",
        "crawler/adapters/api.py",
        "crawler/scripts/healthcheck.py",
        "crawler/scripts/mac_eimzo_daemon.py",
    ]
    # Forbidden sinks only — match the three ways a full response/headers
    # dict actually leaks (RISK-3 evidence in DECISIONS.md).
    #   resp.json() / response.json() / resp.text / response.text
    #   .headers   (but NOT cfg.headers=... assignment in api.py — that writes, not reads)
    FORBIDDEN = re.compile(
        r"(logger\.\w+|print)\s*\([^)]*"
        r"(\bresp\.(?:json|text|headers)\b"
        r"|\bresponse\.(?:json|text|headers)\b"
        r"|\brequest\.headers\b)"
    )

    @pytest.mark.parametrize("rel", FILES)
    def test_no_forbidden_log_sinks(self, rel):
        path = REPO_ROOT / rel
        assert path.exists(), "file listed in DoD gate is missing: %s" % rel
        lines = path.read_text(encoding="utf-8").splitlines()
        offenders = []
        for i, line in enumerate(lines, 1):
            if self.FORBIDDEN.search(line):
                offenders.append("%s:%d: %s" % (rel, i, line.strip()))
        assert not offenders, "token-leakage guard tripped:\n" + "\n".join(offenders)
