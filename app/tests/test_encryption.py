"""
Tests for encryption service.
"""

import pytest

from app.core.encryption import encryption_service


def test_encrypt_decrypt_string():
    """Test encrypting and decrypting a string."""
    plaintext = "sensitive_data_123"

    # Encrypt
    ciphertext = encryption_service.encrypt(plaintext)
    assert ciphertext != plaintext
    assert len(ciphertext) > 0

    # Decrypt
    decrypted = encryption_service.decrypt(ciphertext)
    assert decrypted == plaintext


def test_encrypt_empty_string():
    """Test encrypting an empty string."""
    ciphertext = encryption_service.encrypt("")
    assert ciphertext == ""


def test_decrypt_empty_string():
    """Test decrypting an empty string."""
    plaintext = encryption_service.decrypt("")
    assert plaintext == ""


def test_decrypt_invalid_data():
    """Test decrypting invalid data raises error."""
    with pytest.raises(ValueError):
        encryption_service.decrypt("invalid_encrypted_data")


def test_encrypt_dict_fields():
    """Test encrypting specific fields in a dictionary."""
    data = {"public_field": "public_data", "secret_field": "secret_data", "another_secret": "more_secrets"}

    encrypted = encryption_service.encrypt_dict_fields(data, ["secret_field", "another_secret"])

    # Public field should remain unchanged
    assert encrypted["public_field"] == "public_data"

    # Secret fields should be encrypted
    assert encrypted["secret_field"] != "secret_data"
    assert encrypted["another_secret"] != "more_secrets"


def test_decrypt_dict_fields():
    """Test decrypting specific fields in a dictionary."""
    # First encrypt
    data = {"public_field": "public_data", "secret_field": "secret_data"}

    encrypted = encryption_service.encrypt_dict_fields(data, ["secret_field"])

    # Then decrypt
    decrypted = encryption_service.decrypt_dict_fields(encrypted, ["secret_field"])

    assert decrypted["secret_field"] == "secret_data"
    assert decrypted["public_field"] == "public_data"
