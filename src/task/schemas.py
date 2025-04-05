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

class UserInfo(BaseModel):
    user_id: int
    full_name: str
    shift: str

class TaskResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    status: TaskStatus
    priority: TaskPriority
    due_date: Optional[datetime]
    author: UserInfo
    assignee: UserInfo
    created_at: datetime
    updated_at: datetime

class TaskHistoryResponse(BaseModel):
    event: str
    changed_at: datetime
    user_id: int
    old_status: Optional[TaskStatus]
    new_status: Optional[TaskStatus]

    class Config:
        from_attributes = True

class TaskUpdate(TaskCreate):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[TaskPriority] = None
    due_date: Optional[datetime] = None
    assignee_id: Optional[int] = None
    status: Optional[TaskStatus] = None

class ReassignTaskRequest(BaseModel):
    new_assignee_id: int
    comment: Optional[str] = None