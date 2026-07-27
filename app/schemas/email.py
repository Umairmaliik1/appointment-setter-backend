from typing import Optional

from pydantic import BaseModel, EmailStr

class SubmitEmailDto(BaseModel):
    phoneNumber: Optional[str] = None
    email: Optional[EmailStr] = None
    message: Optional[str] = None
