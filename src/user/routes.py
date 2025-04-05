import json
import time
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File, status, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.db.database import get_db
from src.auth.auth import get_current_user
from src.db.models import User
from src.user.schemas import UserProfile, UserUpdate
import aiofiles
import os
from src.core.config import settings

router = APIRouter(prefix="/user", tags=["user"])

@router.get("/profile", response_model=UserProfile)
async def get_profile(current_user: User = Depends(get_current_user)):
    return current_user

@router.put("/profile", response_model=UserProfile)
async def update_profile(
    user_update: UserUpdate = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    photo: UploadFile = File(None)
):
    try:
        user_update_dict = json.loads(user_update)
        user_update_obj = UserUpdate(**user_update_dict)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Логируем для отладки
    print(f"Parsed user_update: {user_update_obj}")

    # Проверка и обновление username
    if user_update_obj.username and user_update_obj.username != current_user.username:
        existing_user = await db.execute(
            select(User).where(User.username == user_update_obj.username)
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Username already taken")
        current_user.username = user_update_obj.username  # Обновляем username

    # Обновление остальных полей
    if user_update_obj.full_name is not None:
        current_user.full_name = user_update_obj.full_name
    if user_update_obj.email is not None:
        current_user.email = user_update_obj.email

    # Обработка фото
    if photo:
        upload_dir = settings.UPLOAD_DIR
        os.makedirs(upload_dir, exist_ok=True)
        file_ext = os.path.splitext(photo.filename)[1]
        filename = f"avatar_{current_user.user_id}_{int(time.time())}{file_ext}"  # Используем time.time()
        file_path = os.path.join(upload_dir, filename)
        
        async with aiofiles.open(file_path, "wb") as buffer:
            await buffer.write(await photo.read())
        
        current_user.avatar_url = filename

    await db.commit()
    await db.refresh(current_user)
    return current_user

@router.get("/search", response_model=list[UserProfile])
async def search_users(
    username: Optional[str] = None,
    full_name: Optional[str] = None,
    email: Optional[str] = None,
    role_id: Optional[int] = None,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(User).where(User.is_deleted == False)
    
    if username:
        query = query.where(User.username.ilike(f"%{username}%"))
    if full_name:
        query = query.where(User.full_name.ilike(f"%{full_name}%"))
    if email:
        query = query.where(User.email.ilike(f"%{email}%"))
    if role_id:
        query = query.where(User.role_id == role_id)
    
    result = await db.execute(query.order_by(User.username).limit(limit))
    return result.scalars().all()

@router.get("/{user_id}", response_model=UserProfile)
async def get_user_profile(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(
        select(User).where(
            User.user_id == user_id,
            User.is_deleted == False
        )
    )
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user