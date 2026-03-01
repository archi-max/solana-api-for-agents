"""Tests for app/utils/api_key.py — key generation, verification, prefix extraction."""

import pytest
from app.utils.api_key import generate_api_key, verify_api_key, extract_prefix


class TestGenerateApiKey:
    def test_returns_tuple_of_three(self):
        result = generate_api_key()
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_full_key_starts_with_co(self):
        full_key, prefix, hashed = generate_api_key()
        assert full_key.startswith("co_")

    def test_prefix_starts_with_co(self):
        full_key, prefix, hashed = generate_api_key()
        assert prefix.startswith("co_")

    def test_prefix_is_subset_of_full_key(self):
        full_key, prefix, hashed = generate_api_key()
        assert full_key.startswith(prefix)

    def test_prefix_format(self):
        """Prefix should be co_ + 8 hex chars."""
        _, prefix, _ = generate_api_key()
        parts = prefix.split("_")
        assert parts[0] == "co"
        assert len(parts[1]) == 8
        # Should be valid hex
        int(parts[1], 16)

    def test_full_key_has_at_least_three_parts(self):
        """Key is co_{hex}_{secret}, but secret may contain underscores."""
        full_key, _, _ = generate_api_key()
        parts = full_key.split("_")
        assert len(parts) >= 3
        assert parts[0] == "co"

    def test_hashed_key_is_bcrypt(self):
        _, _, hashed = generate_api_key()
        assert hashed.startswith("$2")  # bcrypt prefix

    def test_keys_are_unique(self):
        keys = [generate_api_key()[0] for _ in range(5)]
        assert len(set(keys)) == 5

    def test_prefixes_are_unique(self):
        prefixes = [generate_api_key()[1] for _ in range(5)]
        assert len(set(prefixes)) == 5


class TestVerifyApiKey:
    def test_valid_key_verifies(self):
        full_key, _, hashed = generate_api_key()
        assert verify_api_key(full_key, hashed) is True

    def test_wrong_key_fails(self):
        _, _, hashed = generate_api_key()
        assert verify_api_key("co_wrong_key", hashed) is False

    def test_tampered_key_fails(self):
        full_key, _, hashed = generate_api_key()
        tampered = full_key + "X"
        assert verify_api_key(tampered, hashed) is False

    def test_empty_key_fails(self):
        _, _, hashed = generate_api_key()
        assert verify_api_key("", hashed) is False

    def test_different_hashes_for_different_keys(self):
        _, _, hash1 = generate_api_key()
        _, _, hash2 = generate_api_key()
        assert hash1 != hash2


class TestExtractPrefix:
    def test_valid_key(self):
        full_key, expected_prefix, _ = generate_api_key()
        assert extract_prefix(full_key) == expected_prefix

    def test_manual_key(self):
        assert extract_prefix("co_a1b2c3d4_secretpart") == "co_a1b2c3d4"

    def test_invalid_format_no_co(self):
        assert extract_prefix("xx_a1b2c3d4_secretpart") is None

    def test_invalid_format_too_few_parts(self):
        assert extract_prefix("co_onlyprefix") is None

    def test_empty_string(self):
        assert extract_prefix("") is None

    def test_single_word(self):
        assert extract_prefix("nounderscores") is None

    def test_key_with_extra_underscores(self):
        """Secret part may contain underscores from urlsafe encoding."""
        result = extract_prefix("co_a1b2c3d4_secret_with_underscores")
        assert result == "co_a1b2c3d4"
