import logging
import os
import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.db.database import get_db
from src.auth.auth import get_current_user
from src.db.models import User
from src.user.schemas import UserProfile, UserUpdate
import aiofiles
from src.core.config import settings
import aioredis
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["user"])

# Добавляем зависимость для Redis
async def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis

async def get_user_update(
    username: Optional[str] = Form(None),
    full_name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    shift: Optional[str] = Form(None),
) -> UserUpdate:
    return UserUpdate(
        username=username,
        full_name=full_name,
        email=email,
        shift=shift
    )

@router.get("/profile", response_model=UserProfile)
async def get_profile(
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Получение профиля текущего пользователя."""
    # Кэширование профиля
    redis_key = f"user_profile:{current_user.user_id}"
    cached_profile = await redis.get(redis_key)
    if cached_profile:
        return json.loads(cached_profile)
    
    await redis.setex(redis_key, settings.CACHE_EXPIRE_SECONDS, json.dumps(current_user.__dict__))
    return current_user

@router.put("/profile", response_model=UserProfile)
async def update_profile(
    user_update: UserUpdate = Depends(get_user_update),
    photo: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Обновление профиля пользователя."""
    redis_key = f"user_profile:{current_user.user_id}"
    await redis.delete(redis_key)

    if user_update.username and user_update.username != current_user.username:
        existing_user = await db.execute(
            select(User).where(User.username == user_update.username)
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Имя пользователя уже занято"
            )
        current_user.username = user_update.username

    if user_update.email and user_update.email != current_user.email:
        existing_user = await db.execute(
            select(User).where(User.email == user_update.email)
        )
        if existing_user.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email уже используется"
            )
        current_user.email = user_update.email

    if user_update.full_name is not None:
        current_user.full_name = user_update.full_name
    if user_update.shift is not None:
        current_user.shift = user_update.shift

    if photo:
        allowed_extensions = {".jpg", ".jpeg", ".png", ".gif"}
        file_ext = os.path.splitext(photo.filename)[1].lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неподдерживаемый формат файла. Допустимые форматы: jpg, jpeg, png, gif"
            )

        upload_dir = settings.UPLOAD_DIR
        os.makedirs(upload_dir, exist_ok=True)
        filename = f"avatar_{current_user.user_id}_{uuid.uuid4()}{file_ext}"
        file_path = os.path.join(upload_dir, filename)

        try:
            async with aiofiles.open(file_path, "wb") as buffer:
                content = await photo.read()
                if len(content) > 5 * 1024 * 1024:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Файл слишком большой. Максимальный размер: 5MB"
                    )
                await buffer.write(content)
        except Exception as e:
            logger.error(f"Ошибка загрузки файла: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Ошибка загрузки файла"
            )

        current_user.avatar_url = f"/uploads/{filename}"

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
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    search_params = f"{username}:{full_name}:{email}:{role_id}:{limit}"
    redis_key = f"user_search:{hash(search_params)}"
    cached_result = await redis.get(redis_key)
    if cached_result:
        return json.loads(cached_result)

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
    users = result.scalars().all()
    await redis.setex(redis_key, 300, json.dumps([u.__dict__ for u in users]))
    
    return users

@router.get("/{user_id}", response_model=UserProfile)
async def get_user_profile(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Получение профиля пользователя по ID."""
    redis_key = f"user_profile:{user_id}"
    
    cached_profile = await redis.get(redis_key)
    if cached_profile:
        return json.loads(cached_profile)

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
            detail="Пользователь не найден"
        )
    await redis.setex(redis_key, 3600, json.dumps(user.__dict__))
    
    return user