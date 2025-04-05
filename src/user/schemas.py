from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from typing import Optional

class UserProfile(BaseModel):
    user_id: int
    username: str
    full_name: str
    email: str
    avatar_url: Optional[str] = None
    role_id: int
    shift: str
    registered_at: datetime
    completed_tasks_count: int
    total_tasks_count: int
    edited_articles_count: int
    is_deleted: bool

    class Config:
        from_attributes = True
        orm_mode = True

class UserUpdate(BaseModel):
    username: Optional[str] = Field(
        None, 
        min_length=3, 
        max_length=50,
        pattern=r"^[a-zA-Z0-9_]+$"
    )
    full_name: Optional[str] = Field(
        None,
        min_length=2,
        max_length=100,
        pattern=r"^[а-яА-ЯёЁ\s\-]+$"
    )
    email: Optional[EmailStr] = Field(None)
    shift: str

class UserSearch(BaseModel):
    limit: int = 10  