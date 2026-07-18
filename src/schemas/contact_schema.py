from pydantic import BaseModel, EmailStr, Field

class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Visitor's full nam")
    email: EmailStr
    message: str = Field(..., min_length=1, max_length=1000)