import os
import uuid
import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.auth.auth import get_current_user
from src.auth.routes import hash_password
from src.db.models import User
from src.db.database import get_db
from src.user.schemas import UserProfile, UserUpdate
from typing import Optional
from src.core.config import settings

router = APIRouter(prefix="/admin", tags=["admin"])

@router.get("/users", response_model=list[UserProfile])
async def get_users(
    role: Optional[int] = None,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role_id != 2:
        raise HTTPException(status_code=403, detail="Не авторизовано")
    
    query = select(User).where(User.is_deleted == False)
    if role:
        query = query.where(User.role_id == role)
    
    query = query.limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()
    return users

@router.put("/users/{user_id}/password", response_model=dict)
async def update_user_password(
    user_id: int,
    new_password: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role_id != 2:
        raise HTTPException(status_code=403, detail="Не авторизовано")
    
    result = await db.execute(select(User).where(User.user_id == user_id, User.is_deleted == False))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    user.hashed_password = hash_password(new_password)
    await db.commit()
    return {"message": "Пароль обновлен"}

@router.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.role_id != 2:
        raise HTTPException(status_code=403, detail="Не авторизовано")
    
    result = await db.execute(select(User).where(User.user_id == user_id, User.is_deleted == False))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    user.is_deleted = True
    user.deleted_at = func.now()
    await db.commit()
    return {"message": "Пользователь помечен как удаленный"}

@router.put("/users/{user_id}", response_model=UserProfile)
async def admin_update_user(
    user_id: int,
    username: Optional[str] = Form(None),
    full_name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    shift: Optional[str] = Form(None),
    photo: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Обновление данных пользователя администратором (форма)"""
    # Проверка прав администратора
    if current_user.role_id != 2:
        raise HTTPException(status_code=403, detail="Forbidden: Admin rights required")
    
    # Поиск пользователя
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Собираем обновления в словарь
    update_data = {
        "username": username,
        "full_name": full_name,
        "email": email,
        "shift": shift
    }
    
    # Обновление username
    if username and username != target_user.username:
        existing = await db.execute(
            select(User).where(
                (User.username == username) & 
                (User.user_id != user_id)
            )
        )
        if existing.scalar():
            raise HTTPException(400, "Username занят")
        target_user.username = username

    # Обновление email
    if email and email != target_user.email:
        existing = await db.execute(
            select(User).where(
                (User.email == email) & 
                (User.user_id != user_id)
            )
        )
        if existing.scalar():
            raise HTTPException(400, "Email занят")
        target_user.email = email

    # Обновление остальных полей
    for field in ["full_name", "shift"]:
        if update_data[field] is not None:
            setattr(target_user, field, update_data[field])

    # Обработка фотографии
    if photo:
        allowed_extensions = {".jpg", ".jpeg", ".png", ".gif"}
        file_ext = os.path.splitext(photo.filename)[1].lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(status_code=400, detail="Unsupported file format. Allowed: jpg, jpeg, png, gif")

        upload_dir = settings.UPLOAD_DIR
        os.makedirs(upload_dir, exist_ok=True)
        filename = f"avatar_{target_user.user_id}_{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(upload_dir, filename)

        try:
            async with aiofiles.open(file_path, "wb") as buffer:
                content = await photo.read()
                if len(content) > 5 * 1024 * 1024:
                    raise HTTPException(status_code=400, detail="File too large. Max size: 5MB")
                await buffer.write(content)
        except Exception as e:
            raise HTTPException(status_code=500, detail="Failed to upload file")

        target_user.avatar_url = f"/uploads/{filename}"
        
    await db.commit()
    await db.refresh(target_user)
    return target_user