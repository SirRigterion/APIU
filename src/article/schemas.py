from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

class ArticleCreate(BaseModel):
    title: str
    content: str 

class ArticleImage(BaseModel):
    id: int
    image_path: str
    
    class Config:
        from_attributes = True

class ArticleResponse(BaseModel):
    id: int
    title: str
    content: str
    author_id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool
    images: List[ArticleImage] = []

    class Config:
        from_attributes = True

class ArticleUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None

class ArticleHistoryResponse(BaseModel):
    id: int
    article_id: int
    user_id: int
    event: str
    changed_at: datetime
    old_title: Optional[str] = None
    new_title: Optional[str] = None
    old_content: Optional[str] = None
    new_content: Optional[str] = None
    
    class Config:
        from_attributes = True