"""
Encryption service for securing sensitive data.
Uses Fernet symmetric encryption (AES-128 in CBC mode).
"""

import base64
import logging

from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.core.config import SECRET_KEY

# Configure logging
logger = logging.getLogger(__name__)


class EncryptionService:
    """Service for encrypting and decrypting sensitive data."""

    def __init__(self):
        """Initialize encryption service with a derived key."""
        # Derive a proper encryption key from SECRET_KEY
        # Using PBKDF2HMAC to derive a 32-byte key suitable for Fernet
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"ai_phone_scheduler_salt",  # In production, use a randomly generated salt stored securely
            iterations=100000,
            backend=default_backend(),
        )

        # Derive key from SECRET_KEY
        key = base64.urlsafe_b64encode(kdf.derive(SECRET_KEY.encode()))
        self.fernet = Fernet(key)
        logger.info("Encryption service initialized")

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext string and return base64 encoded ciphertext.

        Args:
            plaintext: The string to encrypt

        Returns:
            Base64 encoded encrypted string
        """
        try:
            if not plaintext:
                return ""

            # Encrypt the plaintext
            ciphertext = self.fernet.encrypt(plaintext.encode())

            # Return as base64 string for easy storage
            return ciphertext.decode()

        except Exception as e:
            logger.error(f"Error encrypting data: {e}", exc_info=True)
            raise ValueError("Failed to encrypt data")

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt base64 encoded ciphertext and return plaintext.

        Args:
            ciphertext: Base64 encoded encrypted string

        Returns:
            Decrypted plaintext string
        """
        try:
            if not ciphertext:
                return ""

            # Decrypt the ciphertext
            plaintext = self.fernet.decrypt(ciphertext.encode())

            # Return as string
            return plaintext.decode()

        except Exception as e:
            logger.error(f"Error decrypting data: {e}", exc_info=True)
            raise ValueError("Failed to decrypt data - data may be corrupted or key is incorrect")

    def encrypt_dict_fields(self, data: dict, fields_to_encrypt: list) -> dict:
        """
        Encrypt specific fields in a dictionary.

        Args:
            data: Dictionary containing data
            fields_to_encrypt: List of field names to encrypt

        Returns:
            Dictionary with encrypted fields
        """
        encrypted_data = data.copy()

        for field in fields_to_encrypt:
            if field in encrypted_data and encrypted_data[field]:
                try:
                    encrypted_data[field] = self.encrypt(encrypted_data[field])
                except Exception as e:
                    logger.error(f"Failed to encrypt field '{field}': {e}")
                    # Keep original value if encryption fails
                    pass

        return encrypted_data

    def decrypt_dict_fields(self, data: dict, fields_to_decrypt: list) -> dict:
        """
        Decrypt specific fields in a dictionary.

        Args:
            data: Dictionary containing encrypted data
            fields_to_decrypt: List of field names to decrypt

        Returns:
            Dictionary with decrypted fields
        """
        decrypted_data = data.copy()

        for field in fields_to_decrypt:
            if field in decrypted_data and decrypted_data[field]:
                try:
                    decrypted_data[field] = self.decrypt(decrypted_data[field])
                except Exception as e:
                    logger.error(f"Failed to decrypt field '{field}': {e}")
                    # Keep encrypted value if decryption fails
                    pass

        return decrypted_data


# Global encryption service instance
encryption_service = EncryptionService()
