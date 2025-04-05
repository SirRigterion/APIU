from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Body, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.auth.auth import get_current_user
from src.db.models import User, Task, TaskHistory
from src.db.database import get_db
from src.task.schemas import (
    ReassignTaskRequest, 
    TaskCreate, 
    TaskUpdate, 
    TaskResponse, 
    TaskStatus, 
    TaskHistoryResponse
)
from typing import Optional, List
import aiofiles
from src.core.config import settings
import os
from src.task.enums import TaskPriority

router = APIRouter(prefix="/tasks", tags=["tasks"])

ADMIN_ROLE_ID = 2

async def verify_assignee(db: AsyncSession, assignee_id: int) -> None:
    result = await db.execute(
        select(User).where(
            User.user_id == assignee_id,
            User.is_deleted == False
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignee not found"
        )

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate = Body(...),
    images: List[UploadFile] = File(default=[]),
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
                changes=new_task.__dict__.copy()
            )
            db.add(history_entry)

            if images:
                os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
                for image in images:
                    file_ext = os.path.splitext(image.filename)[1].lower()
                    filename = f"task_{new_task.id}_{uuid4().hex}{file_ext}"
                    file_path = os.path.join(settings.UPLOAD_DIR, filename)
                    
                    async with aiofiles.open(file_path, "wb") as f:
                        content = await image.read()
                        await f.write(content)
                    
                    db.add(TaskHistory(
                        task_id=new_task.id,
                        user_id=current_user.user_id,
                        event="IMAGE_ADDED",
                        changes={"image": filename}
                    ))

            await db.commit()
            await db.refresh(new_task)
            
            # Приведение к модели ответа
            return TaskResponse.from_orm(new_task)

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
    images: List[UploadFile] = File(default=[]),
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
                if field == "due_date" and value is not None:
                    value = value.replace(tzinfo=None)
                if field != "assignee_id" and getattr(task, field) != value:
                    changes[field] = {
                        "old": getattr(task, field),
                        "new": value
                    }
                    setattr(task, field, value)

            if changes:
                db.add(TaskHistory(
                    task_id=task_id,
                    user_id=current_user.user_id,
                    event="TASK_UPDATED",
                    changes=changes
                ))

            if images:
                os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
                for image in images:
                    file_ext = os.path.splitext(image.filename)[1].lower()
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
            return TaskResponse.from_orm(task)

    except HTTPException as e:
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
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
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
        .order_by(TaskHistory.changed_at.desc())  # Исправлено на changed_at
        .offset(offset)
        .limit(limit)
    )
    history_entries = result.scalars().all()
    return [TaskHistoryResponse.from_orm(entry) for entry in history_entries]

@router.get("/my", response_model=List[TaskResponse])
async def get_my_tasks(
    status: Optional[TaskStatus] = Query(default=None),
    priority: Optional[TaskPriority] = Query(default=None),
    shift: Optional[str] = Query(default=None, description="Фильтр по смене"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(Task).where(
        (Task.is_deleted == False) &
        ((Task.author_id == current_user.user_id) | 
         (Task.assignee_id == current_user.user_id))
    )

    if status:
        query = query.where(Task.status == status)
    if priority:
        query = query.where(Task.priority == priority)
    if shift:
        query = query.join(User, Task.assignee_id == User.user_id).where(User.shift == shift)

    result = await db.execute(query.order_by(Task.due_date.asc()))
    tasks = result.scalars().all()
    return [TaskResponse.from_orm(task) for task in tasks]

@router.get("/shift", response_model=List[TaskResponse])
async def get_shift_tasks(
    shift: str = Query(..., description="Идентификатор смены"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user_shift_result = await db.execute(
        select(User.shift).where(User.user_id == current_user.user_id)
    )
    user_shift = user_shift_result.scalar()
    
    if not user_shift:
        raise HTTPException(status_code=400, detail="User shift not defined")

    query = select(Task).join(User, Task.assignee_id == User.user_id).where(
        (Task.is_deleted == False) &
        (User.shift == shift) &
        (Task.status != TaskStatus.COMPLETED)
    )

    result = await db.execute(query.order_by(Task.priority.desc(), Task.due_date.asc()))
    tasks = result.scalars().all()
    return [TaskResponse.from_orm(task) for task in tasks]

@router.patch("/{task_id}/reassign", response_model=TaskResponse)
async def reassign_task(
    task_id: int,
    reassign_data: ReassignTaskRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    async with db.begin():
        task = await db.get(Task, task_id)
        if not task or task.is_deleted:
            raise HTTPException(status_code=404, detail="Task not found")

        if current_user.role_id != ADMIN_ROLE_ID and task.author_id != current_user.user_id:
            raise HTTPException(status_code=403, detail="Not enough permissions")

        new_assignee_result = await db.execute(
            select(User).where(
                User.user_id == reassign_data.new_assignee_id,
                User.is_deleted == False
            )
        )
        new_assignee = new_assignee_result.scalar_one_or_none()
        
        if not new_assignee:
            raise HTTPException(status_code=404, detail="New assignee not found")

        current_assignee_result = await db.execute(
            select(User.shift).where(User.user_id == task.assignee_id)
        )
        current_shift = current_assignee_result.scalar()

        if new_assignee.shift != current_shift:
            raise HTTPException(
                status_code=400,
                detail="Cannot reassign to another shift"
            )

        old_assignee_id = task.assignee_id
        task.assignee_id = reassign_data.new_assignee_id

        history_entry = TaskHistory(
            task_id=task_id,
            user_id=current_user.user_id,
            event="REASSIGNED",
            changes={
                "old_assignee": old_assignee_id,
                "new_assignee": reassign_data.new_assignee_id,
                "comment": reassign_data.comment
            }
        )

        try:
            db.add(history_entry)
            await db.commit()
            await db.refresh(task)
            return TaskResponse.from_orm(task)
        except Exception as e:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Reassignment failed: {str(e)}"
            )