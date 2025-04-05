import logging
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
import aioredis

from src.db.database import get_db
from src.auth.schemas import UserCreate, UserLogin
from src.user.schemas import UserProfile
from src.db.models import User
from src.auth.auth import (
    create_access_token, 
    set_auth_cookie, 
    get_current_user, 
    hash_password, 
    verify_password
)
from src.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

async def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis

@router.post("/register", response_model=UserProfile)
async def register(
    user: UserCreate, 
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Регистрация нового пользователя."""
    try:
        # Проверка существующего пользователя
        result = await db.execute(
            select(User).where(
                (User.email == user.email) | 
                (User.username == user.username)
            )
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="Email или имя пользователя уже зарегистрированы"
            )

        # Создание нового пользователя
        hashed_password = hash_password(user.password)
        new_user = User(
            username=user.username,
            full_name=user.full_name,
            email=user.email,
            hashed_password=hashed_password,
            shift=user.shift,
            role_id=1
        )
        
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)

        # Генерация токена и кэширование
        token = create_access_token(data={"sub": user.email})
        await redis.setex(
            f"user_token:{new_user.user_id}", 
            settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, 
            token
        )
        response = Response(status_code=201)
        set_auth_cookie(response, token)
        logger.info(f"Зарегистрирован новый пользователь: {user.email}")
        return new_user

    except Exception as e:
        logger.error(f"Ошибка регистрации: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Ошибка сервера при регистрации"
        )

@router.post("/login")
async def login(
    user: UserLogin, 
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Вход пользователя в систему."""
    try:
        result = await db.execute(
            select(User).where(User.username == user.username)
        )
        db_user = result.scalar_one_or_none()
        
        if not db_user or not verify_password(user.password, db_user.hashed_password):
            logger.warning(f"Неудачная попытка входа для пользователя: {user.username}")
            raise HTTPException(
                status_code=401,
                detail="Неверное имя пользователя или пароль"
            )
        token = create_access_token(data={"sub": db_user.username})
        await redis.setex(
            f"user_session:{db_user.user_id}", 
            settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            token
        )

        response = Response(status_code=200)
        set_auth_cookie(response, token)
        logger.info(f"Успешный вход пользователя: {db_user.username}")
        return response

    except Exception as e:
        logger.error(f"Ошибка входа: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Ошибка сервера при входе в систему"
        )

@router.post("/logout", response_model=dict)
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Выход пользователя из системы."""
    try:
        await redis.delete(f"user_session:{current_user.user_id}")
        response = Response(status_code=200)
        response.delete_cookie(
        key="access_token",
        httponly=False,
        samesite="none",
        secure=False
        )
        logger.info(f"Пользователь {current_user.username} вышел из системы")
        return {"message": "Выход выполнен успешно"}

    except Exception as e:
        logger.error(f"Ошибка выхода: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Ошибка сервера при выходе из системы"
        )