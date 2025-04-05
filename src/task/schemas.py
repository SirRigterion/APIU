from pydantic import BaseModel
from datetime import datetime
from typing import Optional
from src.task.enums import TaskStatus, TaskPriority

class TaskCreate(BaseModel):
    title: str
    description: Optional[str]
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: Optional[datetime]
    assignee_id: int

class TaskResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    status: TaskStatus
    priority: TaskPriority
    due_date: Optional[datetime]
    author_id: int
    assignee_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class TaskHistoryResponse(BaseModel):
    event: str
    changed_at: datetime
    user_id: int
    old_status: Optional[TaskStatus]
    new_status: Optional[TaskStatus]

    class Config:
        from_attributes = True