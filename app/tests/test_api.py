"""
Tests for API endpoints.
"""

from fastapi import status


def test_root_endpoint(client):
    """Test the root endpoint returns correct response."""
    response = client.get("/")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "message" in data
    assert "version" in data
    assert data["version"] == "1.0.0"


def test_health_check_endpoint(client):
    """Test the health check endpoint."""
    response = client.get("/api/v1/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"


def test_api_docs_available(client):
    """Test that API documentation is available."""
    response = client.get("/docs")
    assert response.status_code == status.HTTP_200_OK
