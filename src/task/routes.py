from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Body, File, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import case, func, update
from src.auth.auth import get_current_user
from src.db.models import User, Task, TaskHistory
from src.db.database import get_db
from src.task.schemas import TaskCreate, TaskResponse, TaskStatus, TaskHistoryResponse
from typing import Optional, List
import aiofiles
from src.core.config import settings
import os
from src.task.enums import TaskPriority

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    task_data: TaskCreate = Body(...),
    images: List[UploadFile] = File([]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Проверка существования исполнителя
    assignee = await db.execute(
        select(User).where(
            User.user_id == task_data.assignee_id,
            User.is_deleted == False
        )
    )
    if not assignee.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Assignee not found"
        )

    # Создание задачи
    new_task = Task(
        **task_data.dict(exclude={"assignee_id"}),
        author_id=current_user.user_id,
        assignee_id=task_data.assignee_id,
        due_date=task_data.due_date.replace(tzinfo=None) if task_data.due_date else None
    )
    
    try:
        db.add(new_task)
        await db.commit()
        await db.refresh(new_task)
        
        # Запись в историю
        history_entry = TaskHistory(
            task_id=new_task.id,
            user_id=current_user.user_id,
            event="TASK_CREATED",
            changes={"status": {"old": None, "new": new_task.status.value}}
        )
        db.add(history_entry)

        # Сохранение изображений
        if images:
            os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
            for image in images:
                file_ext = os.path.splitext(image.filename)[1]
                filename = f"task_{new_task.id}_{uuid4().hex}{file_ext}"
                file_path = os.path.join(settings.UPLOAD_DIR, filename)
                
                async with aiofiles.open(file_path, "wb") as f:
                    await f.write(await image.read())
                
                # Запись в историю о добавлении изображения
                db.add(TaskHistory(
                    task_id=new_task.id,
                    user_id=current_user.user_id,
                    event="IMAGE_ADDED",
                    changes={"image": filename}
                ))

        await db.commit()
        return new_task

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/", response_model=List[TaskResponse])
async def get_tasks(
    title: Optional[str] = None,
    assignee_id: Optional[int] = None,
    status: Optional[TaskStatus] = None,
    priority: Optional[TaskPriority] = None,
    offset: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(Task).where(Task.is_deleted == False)
    
    filters = []
    if title: filters.append(Task.title.ilike(f"%{title}%"))
    if assignee_id: filters.append(Task.assignee_id == assignee_id)
    if status: filters.append(Task.status == status)
    if priority: filters.append(Task.priority == priority)
    
    if filters:
        query = query.where(*filters)
    
    result = await db.execute(
        query.order_by(Task.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()

@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    task_data: TaskCreate = Body(...),
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

    # Проверка прав
    if current_user.role_id != 2 and task.author_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions"
        )

    # Обновление полей
    update_data = task_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        if field == "due_date" and value:
            value = value.replace(tzinfo=None)
        setattr(task, field, value)

    # Запись изменений в историю
    history_entry = TaskHistory(
        task_id=task_id,
        user_id=current_user.user_id,
        event="TASK_UPDATED",
        changes=update_data
    )
    
    try:
        db.add(history_entry)
        await db.commit()
        await db.refresh(task)
        return task
    
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.patch("/{task_id}/status", response_model=TaskResponse)
async def update_task_status(
    task_id: int,
    new_status: TaskStatus,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = await db.get(Task, task_id)
    if not task or task.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found"
        )

    # Валидация перехода статусов
    valid_transitions = {
        TaskStatus.ACTIVE: [TaskStatus.POSTPONED, TaskStatus.COMPLETED],
        TaskStatus.POSTPONED: [TaskStatus.ACTIVE, TaskStatus.COMPLETED],
        TaskStatus.COMPLETED: [TaskStatus.ACTIVE]
    }
    
    if new_status not in valid_transitions[task.status]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status transition from {task.status} to {new_status}"
        )

    # Запись в историю
    history_entry = TaskHistory(
        task_id=task_id,
        user_id=current_user.user_id,
        event="STATUS_CHANGED",
        changes={
            "status": {
                "old": task.status.value,
                "new": new_status.value
            }
        }
    )
    
    task.status = new_status
    db.add(history_entry)
    
    try:
        await db.commit()
        await db.refresh(task)
        return task
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/{task_id}/history", response_model=List[TaskHistoryResponse])
async def get_task_history(
    task_id: int,
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
    )
    return result.scalars().all()

@router.get("/stats", response_model=dict)
async def get_task_statistics(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(
            Task.status,
            func.count(Task.id),
            func.sum(case((Task.priority == TaskPriority.HIGH, 1), else_=0)),
            func.sum(case((Task.priority == TaskPriority.MEDIUM, 1), else_=0)),
            func.sum(case((Task.priority == TaskPriority.LOW, 1), else_=0))
        )
        .where(Task.is_deleted == False)
        .group_by(Task.status)
    )
    
    stats = {}
    for row in result.all():
        stats[row[0].value] = {
            "total": row[1],
            "high_priority": row[2],
            "medium_priority": row[3],
            "low_priority": row[4]
        }
    
    return stats