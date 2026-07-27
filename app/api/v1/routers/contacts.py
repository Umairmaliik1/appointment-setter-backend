"""
Contact/Lead submission API routes.
"""

import uuid
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, HTTPException, status

from app.api.v1.schemas.contact import ContactCreate, ContactResponse
from app.services.store import store

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.post("", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(contact_data: ContactCreate):
    """
    Create a new contact/lead submission.

    At least one of email or phone_number must be provided.
    Both fields are optional, but at least one is required.
    """
    try:
        # Generate a unique ID for the contact
        contact_id = uuid.uuid4()
        created_at = datetime.now(timezone.utc)

        # Create contact record
        contact_record: Dict[str, str | None] = {
            "id": str(contact_id),
            "email": str(contact_data.email) if contact_data.email else None,
            "phone_number": contact_data.phone_number,
            "created_at": created_at.isoformat(),
        }

        await store.create_contact(contact_record)

        return ContactResponse(
            id=contact_id,
            email=str(contact_data.email) if contact_data.email else None,
            phone_number=contact_data.phone_number,
            created_at=created_at,
        )

    except ValueError as e:
        # Handle validation errors from Pydantic
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create contact: {str(e)}")
