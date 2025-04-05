from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Body, File, Path, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.auth.auth import get_current_user
from src.db.models import TaskHistory, User, Task
from src.db.database import get_db
from src.task.schemas import (
    ReassignTaskRequest, 
    TaskCreate, 
    TaskUpdate, 
    TaskResponse, 
    TaskHistoryResponse
)
from src.task.enums import TaskPriority, TaskStatus
from typing import List, Optional
import aiofiles
from src.core.config import settings
import os

router = APIRouter(prefix="/tasks", tags=["tasks"])
ADMIN_ROLE_ID = 2

async def save_uploaded_file(file: UploadFile, task_id: int, upload_dir: str) -> str:
    """Общая функция для сохранения файлов"""
    file_ext = os.path.splitext(file.filename)[1].lower()
    filename = f"task_{task_id}_{uuid4().hex}{file_ext}"
    file_path = os.path.join(upload_dir, filename)
    
    async with aiofiles.open(file_path, "wb") as buffer:
        await buffer.write(await file.read())
    
    return filename

async def verify_assignee(db: AsyncSession, assignee_id: int) -> User:
    """Проверка существования исполнителя"""
    result = await db.execute(
        select(User).where(
            User.user_id == assignee_id,
            User.is_deleted == False
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Исполнитель не найден")
    return user

@router.post("/", response_model=TaskResponse)
async def create_task(
    task_data: TaskCreate = Body(...),
    images: List[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        await verify_assignee(db, task_data.assignee_id)
        
        task_dict = task_data.dict()
        if task_dict.get("due_date"):
            task_dict["due_date"] = task_dict["due_date"].replace(tzinfo=None)

        new_task = Task(
            **task_dict,
            author_id=current_user.user_id
        )

        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        
        async with db.begin():
            db.add(new_task)
            await db.flush()

            history_entry = TaskHistory(
                task_id=new_task.id,
                user_id=current_user.user_id,
                event="TASK_CREATED",
                changes=task_dict
            )
            db.add(history_entry)

            for image in images:
                filename = await save_uploaded_file(image, new_task.id, settings.UPLOAD_DIR)
                db.add(TaskHistory(
                    task_id=new_task.id,
                    user_id=current_user.user_id,
                    event="IMAGE_ADDED",
                    changes={"image": filename}
                ))

            await db.commit()
            await db.refresh(new_task)
            return TaskResponse.from_orm(new_task)

    except HTTPException as e:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Не удалось создать задачу: {str(e)}")

@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int = Path(...),
    task_data: TaskUpdate = Body(...),
    images: List[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        result = await db.execute(
            select(Task).where(Task.id == task_id, Task.is_deleted == False)
        )
        task = result.scalar_one_or_none()
        
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        
        if current_user.role_id != ADMIN_ROLE_ID and task.author_id != current_user.user_id:
            raise HTTPException(status_code=403, detail="Недостаточно прав")

        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        
        async with db.begin():
            changes = {}
            update_data = task_data.dict(exclude_unset=True)
            
            if "assignee_id" in update_data:
                await verify_assignee(db, update_data["assignee_id"])
                if task.assignee_id != update_data["assignee_id"]:
                    changes["assignee_id"] = {"old": task.assignee_id, "new": update_data["assignee_id"]}
                    task.assignee_id = update_data["assignee_id"]

            for field, value in update_data.items():
                if field != "assignee_id":
                    if field == "due_date" and value is not None:
                        value = value.replace(tzinfo=None)
                    current_value = getattr(task, field)
                    if current_value != value:
                        changes[field] = {"old": current_value, "new": value}
                        setattr(task, field, value)

            if changes:
                db.add(TaskHistory(
                    task_id=task_id,
                    user_id=current_user.user_id,
                    event="TASK_UPDATED",
                    changes=changes
                ))

            for image in images:
                filename = await save_uploaded_file(image, task_id, settings.UPLOAD_DIR)
                db.add(TaskHistory(
                    task_id=task_id,
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
        raise HTTPException(status_code=500, detail=f"Не удалось обновить задачу: {str(e)}")

@router.get("/{task_id}/history", response_model=List[TaskHistoryResponse])
async def get_task_history(
    task_id: int = Path(...),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    task = await db.get(Task, task_id)
    if not task or task.is_deleted:
        raise HTTPException(status_code=404, detail="Задача не найдена")

    result = await db.execute(
        select(TaskHistory)
        .where(TaskHistory.task_id == task_id)
        .order_by(TaskHistory.changed_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [TaskHistoryResponse.from_orm(entry) for entry in result.scalars().all()]

@router.get("/my", response_model=List[TaskResponse])
async def get_my_tasks(
    status: Optional[TaskStatus] = Query(default=None),
    priority: Optional[TaskPriority] = Query(default=None),
    shift: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(Task).where(
        Task.is_deleted == False,
        (Task.author_id == current_user.user_id) | (Task.assignee_id == current_user.user_id)
    )

    if status:
        query = query.where(Task.status == status)
    if priority:
        query = query.where(Task.priority == priority)
    if shift:
        query = query.join(User, Task.assignee_id == User.user_id).where(User.shift == shift)

    result = await db.execute(query.order_by(Task.due_date.asc()))
    return [TaskResponse.from_orm(task) for task in result.scalars().all()]

@router.get("/shift", response_model=List[TaskResponse])
async def get_shift_tasks(
    shift: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    user_shift = (await db.execute(
        select(User.shift).where(User.user_id == current_user.user_id)
    )).scalar()
    
    if not user_shift:
        raise HTTPException(status_code=400, detail="Смена пользователя не определена")

    result = await db.execute(
        select(Task)
        .join(User, Task.assignee_id == User.user_id)
        .where(
            Task.is_deleted == False,
            User.shift == shift,
            Task.status != TaskStatus.COMPLETED
        )
        .order_by(Task.priority.desc(), Task.due_date.asc())
    )
    return [TaskResponse.from_orm(task) for task in result.scalars().all()]

@router.patch("/{task_id}/reassign", response_model=TaskResponse)
async def reassign_task(
    task_id: int = Path(...),
    reassign_data: ReassignTaskRequest = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        async with db.begin():
            task = await db.get(Task, task_id)
            if not task or task.is_deleted:
                raise HTTPException(status_code=404, detail="Задача не найдена")

            if current_user.role_id != ADMIN_ROLE_ID and task.author_id != current_user.user_id:
                raise HTTPException(status_code=403, detail="Недостаточно прав")

            new_assignee = await verify_assignee(db, reassign_data.new_assignee_id)
            current_shift = (await db.execute(
                select(User.shift).where(User.user_id == task.assignee_id)
            )).scalar()

            if new_assignee.shift != current_shift:
                raise HTTPException(status_code=400, detail="Нельзя переназначить на другую смену")

            old_assignee_id = task.assignee_id
            task.assignee_id = reassign_data.new_assignee_id

            db.add(TaskHistory(
                task_id=task_id,
                user_id=current_user.user_id,
                event="REASSIGNED",
                changes={
                    "old_assignee": old_assignee_id,
                    "new_assignee": reassign_data.new_assignee_id,
                    "comment": reassign_data.comment
                }
            ))

            await db.commit()
            await db.refresh(task)
            return TaskResponse.from_orm(task)

    except HTTPException as e:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Переназначение не удалось: {str(e)}")