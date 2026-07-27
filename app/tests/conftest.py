"""
Pytest configuration and fixtures for testing.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI application."""
    return TestClient(app)


@pytest.fixture
def mock_tenant_id():
    """Return a mock tenant ID for testing."""
    return "test-tenant-123"


@pytest.fixture
def mock_appointment_data():
    """Return mock appointment data for testing."""
    return {
        "tenant_id": "test-tenant-123",
        "customer_name": "John Doe",
        "customer_phone": "+1234567890",
        "customer_email": "john@example.com",
        "service_type": "Plumbing",
        "service_address": "123 Main St",
        "appointment_datetime": "2024-12-01T10:00:00Z",
        "service_details": "Fix leaky faucet",
    }


@pytest.fixture
def mock_twilio_integration():
    """Return mock Twilio integration data for testing."""
    return {"account_sid": "AC" + "x" * 32, "auth_token": "test_auth_token_123", "phone_number": "+1234567890"}
