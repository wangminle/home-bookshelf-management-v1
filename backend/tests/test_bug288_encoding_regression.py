"""BUG-288：异常回显中的等价 URL 编码不得泄露凭据。"""
from __future__ import annotations

import itertools
import urllib.error
from unittest.mock import patch

import pytest

from app.services.metadata import http


@pytest.mark.parametrize("fetch", [http.get_json, http.get_text], ids=["json", "text"])
@pytest.mark.parametrize("error_kind", ["network", "http", "timeout"])
@pytest.mark.parametrize("echoed_secret", [
    "synthetic%2bsecret%2f2026%3d",
    "synthetic%2Bsecret%2f2026%3D",
    "synthetic+secret%2F2026=",
    "%73ynthetic%2bsecret/2026%3d",
])
def test_request_events_mask_alternate_encoding(fetch, error_kind, echoed_secret):
    url = "https://example.invalid/books?api_key=synthetic%2Bsecret%2F2026%3D&q=isbn%3A123"
    echoed = "https://example.invalid/books?api_key=" + echoed_secret + "&q=isbn%3A123"
    if error_kind == "http":
        exc = urllib.error.HTTPError(url, 429, "failed " + echoed, None, None)
    elif error_kind == "network":
        exc = urllib.error.URLError("failed " + echoed)
    else:
        exc = TimeoutError("failed " + echoed)
    events = []
    http.clear_response_listeners()
    http.add_response_listener(events.append)
    try:
        with patch.object(http.urllib.request, "urlopen", side_effect=exc):
            assert fetch(url) is None
    finally:
        http.clear_response_listeners()
    assert len(events) == 1
    assert "api_key=***&q=isbn%3A123" in events[0]["error"]
    assert echoed_secret not in str(events[0])
    assert events[0]["status"] == (429 if error_kind == "http" else None)


def test_masks_every_combination_of_percent_hex_case_and_partial_encoding():
    url = "https://example.invalid/?api_key=a%2Bb%2Fc%3D&q=keep"
    # 每个位置独立变化，不止是整体转大写/小写。
    for parts in itertools.product(("a", "%61"), ("+", "%2B", "%2b"),
                                   ("b", "%62"), ("/", "%2F", "%2f"),
                                   ("c", "%63"), ("=", "%3D", "%3d")):
        secret = "".join(parts)
        exc = RuntimeError("failed credential=" + secret + " diagnostic=Keep%2FCase")
        assert http._error_message(exc, url) == (
            "RuntimeError: failed credential=*** diagnostic=Keep%2FCase")


@pytest.mark.parametrize("echoed_secret", [
    "alpha+beta%2fgamma", "alpha%20beta/gamma", "alpha beta/gamma",
    "alpha%20beta%2Fgamma",
])
def test_space_percent_encoding_and_plus_form_are_masked(echoed_secret):
    url = "https://example.invalid/?token=alpha%20beta%2Fgamma&q=keep+spaces"
    assert http._error_message(RuntimeError("value=" + echoed_secret), url) == (
        "RuntimeError: value=***")


@pytest.mark.parametrize("echoed_secret", [
    "%e5%af%86%e9%92%a5%2b2026", "密钥%2B2026", "%E5%af%86钥+2026",
])
def test_utf8_encoded_keys_are_masked(echoed_secret):
    url = "https://example.invalid/?%61pi_key=%E5%AF%86%E9%92%A5%2B2026"
    assert http._error_message(RuntimeError("value=" + echoed_secret), url) == (
        "RuntimeError: value=***")


def test_secret_matching_is_case_sensitive_and_preserves_other_text():
    url = "https://example.invalid/?key=AbC%2FD&token=other%2Bkey&q=abc%2Fd"
    exc = RuntimeError("AbC%2fD other+key ABC/D abc%2Fd q=Keep%2fCase")
    assert http._error_message(exc, url) == (
        "RuntimeError: *** *** ABC/D abc%2Fd q=Keep%2fCase")


def test_overlapping_keys_are_fully_masked_longest_first():
    url = "https://example.invalid/?key=ab&token=ab%2Fcd"
    assert http._error_message(RuntimeError("ab%2fcd ab"), url) == (
        "RuntimeError: *** ***")


@pytest.mark.parametrize("echoed_secret", ["%", "%25"])
def test_percent_key_does_not_leave_encoded_tail(echoed_secret):
    assert http._error_message(RuntimeError("value=" + echoed_secret),
                               "https://example.invalid/?token=%25") == (
        "RuntimeError: value=***")


@pytest.mark.parametrize("echoed_secret", ["synthetic%ffkey", "synthetic%FFkey", "synthetic\ufffdkey"])
def test_invalid_utf8_key_masks_raw_and_replacement_decoding(echoed_secret):
    assert http._error_message(RuntimeError("value=" + echoed_secret),
                               "https://example.invalid/?key=synthetic%FFkey") == (
        "RuntimeError: value=***")


def test_regex_metacharacters_in_keys_are_literal():
    url = "https://example.invalid/?key=a%2E%2A%5Bb%5D%24"
    assert http._error_message(RuntimeError("a.*[b]$ axb"), url) == (
        "RuntimeError: *** axb")


def test_generated_mask_is_not_redacted_again_by_another_key():
    assert http._error_message(RuntimeError("value=abc%2fdef other=*"),
                               "https://example.invalid/?key=abc%2Fdef&token=*") == (
        "RuntimeError: value=*** other=***")


def test_overlapping_secrets_from_different_positions_are_fully_masked():
    assert http._error_message(RuntimeError("aabcdef"),
                               "https://example.invalid/?key=aabc&token=abcdef") == (
        "RuntimeError: ***")


def test_key_value_matching_another_parameter_does_not_leave_its_secret_tail():
    url = "https://example.invalid/?key=token%3Dab&token=abcdef&q=keep"
    assert http._error_message(RuntimeError("failed request " + url), url) == (
        "RuntimeError: failed request https://example.invalid/?key=***&***&q=keep")


def test_longer_decoded_key_does_not_hide_a_longer_encoded_match():
    assert http._error_message(RuntimeError("%2B%2B"),
                               "https://example.invalid/?key=%252B&token=%2B%2B") == (
        "RuntimeError: ***")
