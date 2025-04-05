import os
from typing import List, Optional
import uuid
from datetime import datetime, timedelta, timezone
import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Path, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.auth.auth import get_current_user
from src.db.models import Task, TaskHistory, User
from src.db.database import get_db
from src.core.config import settings
from src.task.enums import TaskPriority, TaskStatus
from src.task.schemas import ReassignTaskRequest, TaskHistoryResponse, TaskResponse
from sqlalchemy.orm import selectinload, joinedload

router = APIRouter(prefix="/tasks", tags=["tasks"])
ADMIN_ROLE_ID = 2

async def save_uploaded_file(file: UploadFile, task_id: int, upload_dir: str) -> str:
    file_ext = os.path.splitext(file.filename)[1].lower()
    unique_name = f"{uuid.uuid4().hex}{file_ext}"
    file_path = os.path.join(upload_dir, f"task_{task_id}_{unique_name}")
    
    async with aiofiles.open(file_path, "wb") as buffer:
        await buffer.write(await file.read())
    
    return file_path

async def verify_assignee(db: AsyncSession, assignee_id: int) -> User:
    result = await db.execute(
        select(User).where(User.user_id == assignee_id, User.is_deleted == False)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Исполнитель не найден")
    return user

@router.post("/", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    title: str = Form(..., min_length=3, max_length=255),
    description: Optional[str] = Form(None, max_length=5000),
    assignee_id: int = Form(...),
    due_date: Optional[datetime] = Form(None),
    status: TaskStatus = Form(default=TaskStatus.ACTIVE),
    priority: TaskPriority = Form(default=TaskPriority.MEDIUM),
    images: List[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Создание новой задачи."""
    if due_date:
        due_date = due_date.astimezone(timezone.utc).replace(tzinfo=None)

    try:
        # Проверка существования assignee
        await verify_assignee(db, assignee_id)

        # Формирование данных задачи
        task_data = {
            "title": title,
            "description": description,
            "assignee_id": assignee_id,
            "due_date": due_date,
            "author_id": current_user.user_id,
            "status": status,  # Перечисление TaskStatus
            "priority": priority  # Перечисление TaskPriority
        }
        task = Task(**task_data)
        db.add(task)
        await db.flush()

        # Обработка изображений
        if images:
            os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
            for image in images:
                file_path = await save_uploaded_file(image, task.id, settings.UPLOAD_DIR)
                db.add(TaskHistory(
                    task_id=task.id,
                    user_id=current_user.user_id,
                    event="IMAGE_ADDED",
                    changes={"image": file_path}
                ))

        # Запись в историю создания задачи
        history_task_data = task_data.copy()
        if history_task_data["due_date"]:
            history_task_data["due_date"] = history_task_data["due_date"].isoformat()

        db.add(TaskHistory(
            task_id=task.id,
            user_id=current_user.user_id,
            event="TASK_CREATED",
            changes=history_task_data
        ))

        await db.commit()
        await db.refresh(task)
        return task

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при создании задачи: {str(e)}")   

@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    title: Optional[str] = Form(None, min_length=3, max_length=255),
    description: Optional[str] = Form(None, max_length=5000),
    assignee_id: Optional[int] = Form(None),
    due_date: Optional[datetime] = Form(None),
    status: Optional[TaskStatus] = Form(None),
    priority: Optional[TaskPriority] = Form(None),
    images: List[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Обновление задачи."""
    # Получение задачи из базы данных
    result = await db.execute(
        select(Task).options(joinedload(Task.assignee)).where(Task.id == task_id)
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Обновление полей задачи, если они переданы
    if title is not None:
        task.title = title
    if description is not None:
        task.description = description
    if assignee_id is not None:
        await verify_assignee(db, assignee_id)
        task.assignee_id = assignee_id
    if due_date is not None:
        task.due_date = due_date.astimezone(timezone.utc).replace(tzinfo=None)
    if status is not None:
        task.status = status
    if priority is not None:
        task.priority = priority

    # Обработка загруженных изображений
    if images:
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        for image in images:
            file_path = await save_uploaded_file(image, task.id, settings.UPLOAD_DIR)
            db.add(TaskHistory(
                task_id=task.id,
                user_id=current_user.user_id,
                event="IMAGE_ADDED",
                changes={"image": file_path}
            ))

    # Логирование изменений в историю
    changes = {}
    if title is not None:
        changes["title"] = title
    if description is not None:
        changes["description"] = description
    if assignee_id is not None:
        changes["assignee_id"] = assignee_id
    if due_date is not None:
        changes["due_date"] = due_date.isoformat()
    if status is not None:
        changes["status"] = status.value
    if priority is not None:
        changes["priority"] = priority.value

    if changes:
        db.add(TaskHistory(
            task_id=task.id,
            user_id=current_user.user_id,
            event="TASK_UPDATED",
            changes=changes
        ))

    # Сохранение изменений в базе данных
    await db.commit()
    await db.refresh(task)

    return task

@router.delete("/{task_id}", response_model=dict)
async def delete_task(
    task_id: int = Path(...),
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
        
        task.is_deleted = True
        task.deleted_at = datetime.utcnow()
        
        db.add(TaskHistory(
            task_id=task.id,
            user_id=current_user.user_id,
            event="TASK_DELETED",
            changes={"title": task.title, "description": task.description}
        ))
        
        await db.commit()
        return {"message": "Задача успешно удалена"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при удалении задачи: {str(e)}")
    
@router.get("/{task_id}/history", response_model=List[TaskHistoryResponse])
async def get_task_history(
    task_id: int = Path(...),
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        result = await db.execute(select(Task).where(Task.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена")
        
        if current_user.role_id != ADMIN_ROLE_ID and task.author_id != current_user.user_id:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        
        result = await db.execute(
            select(TaskHistory)
            .where(TaskHistory.task_id == task_id)
            .order_by(TaskHistory.changed_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при получении истории задачи: {str(e)}")

@router.get("/my", response_model=List[TaskResponse])
async def get_my_tasks(
    status_filter: Optional[TaskStatus] = Query(None),
    priority: Optional[TaskPriority] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(Task).options(
        selectinload(Task.assignee),
        selectinload(Task.author)
    ).where(
        Task.is_deleted == False,
        (Task.author_id == current_user.user_id) | (Task.assignee_id == current_user.user_id)
    )
    if status_filter:
        query = query.where(Task.status == status_filter)
    if priority:
        query = query.where(Task.priority == priority)
    
    result = await db.execute(query.order_by(Task.due_date.asc()))
    return result.scalars().all()

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
    return result.scalars().all()

@router.patch("/{task_id}/reassign", response_model=TaskResponse)
async def reassign_task(
    request: ReassignTaskRequest,
    task_id: int = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Получаем задачу с загруженными связями
    result = await db.execute(
        select(Task)
        .options(
            selectinload(Task.assignee),  # Предзагрузка исполнителя
            selectinload(Task.author)     # Предзагрузка автора
        )
        .where(Task.id == task_id, Task.is_deleted == False)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    
    # Проверка прав
    if current_user.role_id != ADMIN_ROLE_ID and task.author_id != current_user.user_id:
        raise HTTPException(status_code=403, detail="Недостаточно прав")
    
    # Проверка нового исполнителя
    await verify_assignee(db, request.new_assignee_id)
    
    # Фиксируем изменения
    changes = {
        "assignee_id": {
            "old": task.assignee_id,
            "new": request.new_assignee_id
        }
    }
    task.assignee_id = request.new_assignee_id
    
    # Добавляем запись в историю
    db.add(TaskHistory(
        task_id=task.id,
        user_id=current_user.user_id,
        event="TASK_REASSIGNED",
        changes=changes,
        comment=request.comment
    ))
    
    await db.commit()
    await db.refresh(task)
    return task

@router.post("/{task_id}/restore", response_model=TaskResponse)
async def restore_task(
    task_id: int = Path(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        result = await db.execute(
            select(Task).where(
                Task.id == task_id,
                Task.is_deleted == True,
                Task.deleted_at >= datetime.utcnow() - timedelta(days=7)
            )
        )
        task = result.scalar_one_or_none()
        if not task:
            raise HTTPException(status_code=404, detail="Задача не найдена или срок восстановления истек")
        
        if current_user.role_id != ADMIN_ROLE_ID and task.author_id != current_user.user_id:
            raise HTTPException(status_code=403, detail="Недостаточно прав")
        
        task.is_deleted = False
        task.deleted_at = None
        
        db.add(TaskHistory(
            task_id=task.id,
            user_id=current_user.user_id,
            event="TASK_RESTORED",
            changes={"title": task.title, "description": task.description}
        ))
        
        await db.commit()
        await db.refresh(task)
        return task
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка при восстановлении задачи: {str(e)}")