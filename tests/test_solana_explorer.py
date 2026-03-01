"""Tests for app/utils/solana_explorer.py — URL generation for Solana Explorer."""

import pytest
from app.utils.solana_explorer import tx_url, address_url, EXPLORER_BASE


# ---------------------------------------------------------------------------
# tx_url
# ---------------------------------------------------------------------------

class TestTxUrl:
    def test_valid_signature(self):
        sig = "5vVdqy3RV5JkHKuDJQDXQLSqC7nSff6EVCznvUQQmgCA"
        result = tx_url(sig)
        assert result == f"{EXPLORER_BASE}/tx/{sig}?cluster=devnet"

    def test_none_returns_none(self):
        assert tx_url(None) is None

    def test_empty_string_returns_none(self):
        assert tx_url("") is None

    def test_contains_cluster_devnet(self):
        result = tx_url("abc123")
        assert "?cluster=devnet" in result

    def test_base_url_is_solana_explorer(self):
        result = tx_url("abc123")
        assert result.startswith("https://explorer.solana.com/tx/")

    def test_signature_preserved_exactly(self):
        sig = "2uaxAjFqANy7gSVt6YFtfRKbWPECCmTVaRmbzQ3ezbKfBcELG1xkdto525fgcNvvNKiPGRRPteMGnZztEPbRCYvn"
        result = tx_url(sig)
        assert sig in result

    def test_long_signature(self):
        sig = "a" * 128
        result = tx_url(sig)
        assert result is not None
        assert sig in result


# ---------------------------------------------------------------------------
# address_url
# ---------------------------------------------------------------------------

class TestAddressUrl:
    def test_valid_address(self):
        addr = "2CpDbucRFQqEBzTsgV6RQYgciPborCZjL5GsrAAuutps"
        result = address_url(addr)
        assert result == f"{EXPLORER_BASE}/address/{addr}?cluster=devnet"

    def test_none_returns_none(self):
        assert address_url(None) is None

    def test_empty_string_returns_none(self):
        assert address_url("") is None

    def test_contains_cluster_devnet(self):
        result = address_url("SomeAddress123")
        assert "?cluster=devnet" in result

    def test_base_url_is_solana_explorer(self):
        result = address_url("SomeAddress123")
        assert result.startswith("https://explorer.solana.com/address/")

    def test_pda_address_preserved_exactly(self):
        addr = "FCk3KLRXWGD2KF2FzsSLE9YXQHHmwnXjLWDCxt1noRjJ"
        result = address_url(addr)
        assert addr in result

    def test_program_id_address(self):
        pid = "TShUF8MeAKE46dz75je7KQEdAahdRQhS3vN7ffDoEds"
        result = address_url(pid)
        assert f"/address/{pid}?" in result


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_tx_url_whitespace_only(self):
        # Whitespace is truthy in Python, so it should produce a URL
        result = tx_url("   ")
        assert result is not None

    def test_address_url_whitespace_only(self):
        result = address_url("   ")
        assert result is not None

    def test_explorer_base_constant(self):
        assert EXPLORER_BASE == "https://explorer.solana.com"
