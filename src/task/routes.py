from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Body, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import case, func, update
from src.auth.auth import get_current_user
from src.db.models import User, Task, TaskHistory
from src.db.database import get_db
from src.task.schemas import TaskCreate, TaskUpdate, TaskResponse, TaskStatus, TaskHistoryResponse
from typing import Optional, List
import aiofiles
from src.core.config import settings
import os
from src.task.enums import TaskPriority

router = APIRouter(prefix="/tasks", tags=["tasks"])

ADMIN_ROLE_ID = 2

async def verify_assignee(db: AsyncSession, assignee_id: int):
    assignee = await db.execute(
        select(User).where(
            User.user_id == assignee_id,
            User.is_deleted == False
        )
    )
    if not assignee.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignee not found"
        )

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate = Body(...),
    images: List[UploadFile] = File([]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    await verify_assignee(db, task_data.assignee_id)

    new_task = Task(
        **task_data.dict(exclude={"assignee_id"}),
        author_id=current_user.user_id,
        assignee_id=task_data.assignee_id,
        due_date=task_data.due_date.replace(tzinfo=None) if task_data.due_date else None
    )

    try:
        async with db.begin():
            db.add(new_task)
            await db.flush()
            
            history_entry = TaskHistory(
                task_id=new_task.id,
                user_id=current_user.user_id,
                event="TASK_CREATED",
                changes={
                    "title": new_task.title,
                    "description": new_task.description,
                    "status": new_task.status.value,
                    "priority": new_task.priority.value,
                    "assignee_id": new_task.assignee_id,
                    "due_date": new_task.due_date.isoformat() if new_task.due_date else None
                }
            )
            db.add(history_entry)

            if images:
                os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
                for image in images:
                    file_ext = os.path.splitext(image.filename)[1]
                    filename = f"task_{new_task.id}_{uuid4().hex}{file_ext}"
                    file_path = os.path.join(settings.UPLOAD_DIR, filename)
                    
                    async with aiofiles.open(file_path, "wb") as f:
                        await f.write(await image.read())
                    
                    db.add(TaskHistory(
                        task_id=new_task.id,
                        user_id=current_user.user_id,
                        event="IMAGE_ADDED",
                        changes={"image": filename}
                    ))

            await db.commit()
        await db.refresh(new_task)
        return new_task

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create task: {str(e)}"
        )

@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    task_data: TaskUpdate = Body(...),
    images: List[UploadFile] = File([]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = await db.get(Task, task_id)
    if not task or task.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    if current_user.role_id != ADMIN_ROLE_ID and task.author_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )

    try:
        async with db.begin():
            changes = {}
            update_data = task_data.dict(exclude_unset=True)
            
            if "assignee_id" in update_data:
                await verify_assignee(db, update_data["assignee_id"])
                changes["assignee_id"] = {
                    "old": task.assignee_id,
                    "new": update_data["assignee_id"]
                }
                task.assignee_id = update_data["assignee_id"]

            for field, value in update_data.items():
                if field == "due_date" and value:
                    value = value.replace(tzinfo=None)
                if field != "assignee_id" and getattr(task, field) != value:
                    changes[field] = {
                        "old": getattr(task, field),
                        "new": value
                    }
                    setattr(task, field, value)

            if changes:
                history_entry = TaskHistory(
                    task_id=task_id,
                    user_id=current_user.user_id,
                    event="TASK_UPDATED",
                    changes=changes
                )
                db.add(history_entry)

            if images:
                os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
                for image in images:
                    file_ext = os.path.splitext(image.filename)[1]
                    filename = f"task_{task.id}_{uuid4().hex}{file_ext}"
                    file_path = os.path.join(settings.UPLOAD_DIR, filename)
                    
                    async with aiofiles.open(file_path, "wb") as f:
                        await f.write(await image.read())
                    
                    db.add(TaskHistory(
                        task_id=task.id,
                        user_id=current_user.user_id,
                        event="IMAGE_ADDED",
                        changes={"image": filename}
                    ))

            await db.commit()
            await db.refresh(task)
            return task

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update task: {str(e)}"
        )

@router.get("/{task_id}/history", response_model=List[TaskHistoryResponse])
async def get_task_history(
    task_id: int,
    offset: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = await db.get(Task, task_id)
    if not task or task.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )
    
    result = await db.execute(
        select(TaskHistory)
        .where(TaskHistory.task_id == task_id)
        .order_by(TaskHistory.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()