import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from redis import asyncio as aioredis
from redis.asyncio import Redis
import json
from src.auth.auth import get_current_user
from src.db.models import User, Article, ArticleHistory, ArticleImage
from src.db.database import get_db
from src.core.config import settings
from src.article.schemas import ArticleResponse, ArticleHistoryResponse

router = APIRouter(prefix="/articles", tags=["articles"])

async def get_redis(request: Request) -> aioredis.Redis:
    return request.app.state.redis

async def invalidate_article_cache(redis: aioredis.Redis, article_id: int):
    keys = [
        f"article:{article_id}",
        f"article_history:{article_id}:*",
        "articles_list:*"
    ]
    for pattern in keys:
        async for key in redis.scan_iter(pattern):
            await redis.delete(key)

async def save_uploaded_file(file: UploadFile, article_id: int, upload_dir: str) -> str:
    file_ext = os.path.splitext(file.filename)[1]
    unique_name = f"{uuid.uuid4().hex}{file_ext}"
    file_path = os.path.join(upload_dir, f"article_{article_id}_{unique_name}")
    
    async with aiofiles.open(file_path, "wb") as buffer:
        await buffer.write(await file.read())
    
    return file_path

@router.get("/", response_model=List[ArticleResponse])
async def get_articles(
    title: Optional[str] = None,
    author_id: Optional[int] = None,
    offset: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Получение списка статей с кэшированием"""
    cache_key = f"articles_list:{title}:{author_id}:{offset}:{limit}"
    cached_data = await redis.get(cache_key)
    
    if cached_data:
        return json.loads(cached_data)

    query = (
        select(Article)
        .where(Article.is_deleted == False)
        .offset(offset)
        .limit(limit)
    )
    
    if title:
        query = query.where(Article.title.ilike(f"%{title}%"))
    if author_id:
        query = query.where(Article.author_id == author_id)
    
    result = await db.execute(query)
    articles = result.scalars().all()
    
    await redis.setex(cache_key, 300, json.dumps([a.__dict__ for a in articles]))
    return articles

@router.post("/", response_model=ArticleResponse, status_code=status.HTTP_201_CREATED)
async def create_article(
    title: str = Form(..., min_length=3, max_length=255),
    content: str = Form(..., max_length=5000),
    images: List[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Создание новой статьи с инвалидацией кэша"""
    try:
        article = Article(
            title=title,
            content=content,
            author_id=current_user.user_id
        )
        db.add(article)
        await db.flush()

        if images:
            os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
            for image in images:
                file_path = await save_uploaded_file(image, article.id, settings.UPLOAD_DIR)
                article_image = ArticleImage(article_id=article.id, image_path=file_path)
                db.add(article_image)

        history_entry = ArticleHistory(
            article_id=article.id,
            user_id=current_user.user_id,
            event="CREATE",
            new_title=title,
            new_content=content
        )
        db.add(history_entry)
        
        await db.commit()
        await invalidate_article_cache(redis, article.id)
        return article

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка создания статьи"
        )

@router.put("/{article_id}", response_model=ArticleResponse)
async def update_article(
    article_id: int,
    title: Optional[str] = Form(default=None, min_length=3, max_length=255),
    content: Optional[str] = Form(default=None, max_length=5000),
    images: List[UploadFile] = File(default=[]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Обновление статьи с инвалидацией кэша"""
    try:
        result = await db.execute(
            select(Article)
            .where(Article.id == article_id, Article.is_deleted == False)
        )
        article = result.scalar_one_or_none()
        
        if not article:
            raise HTTPException(status_code=404, detail="Статья не найдена")
        
        if article.author_id != current_user.user_id and current_user.role_id != 2:
            raise HTTPException(status_code=403, detail="Доступ запрещен")

        changes_made = False
        old_title = article.title
        old_content = article.content

        if title is not None and title != article.title:
            article.title = title
            changes_made = True
        if content is not None and content != article.content:
            article.content = content
            changes_made = True

        if images:
            os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
            for image in images:
                file_path = await save_uploaded_file(image, article.id, settings.UPLOAD_DIR)
                db.add(ArticleImage(article_id=article.id, image_path=file_path))
            changes_made = True

        if changes_made:
            history_entry = ArticleHistory(
                article_id=article.id,
                user_id=current_user.user_id,
                event="UPDATE",
                old_title=old_title,
                new_title=title,
                old_content=old_content,
                new_content=content
            )
            db.add(history_entry)

        await db.commit()
        await invalidate_article_cache(redis, article.id)
        return article

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка обновления статьи"
        )

@router.delete("/{article_id}", response_model=dict)
async def delete_article(
    article_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Удаление статьи с инвалидацией кэша"""
    try:
        result = await db.execute(
            select(Article)
            .where(Article.id == article_id, Article.is_deleted == False)
        )
        article = result.scalar_one_or_none()
        
        if not article:
            raise HTTPException(status_code=404, detail="Статья не найдена")
        
        if article.author_id != current_user.user_id and current_user.role_id != 2:
            raise HTTPException(status_code=403, detail="Недостаточно прав для выполнения операции")

        article.is_deleted = True
        article.deleted_at = datetime.utcnow()
        
        history_entry = ArticleHistory(
            article_id=article.id,
            user_id=current_user.user_id,
            event="DELETE",
            old_title=article.title,
            old_content=article.content
        )
        db.add(history_entry)
        
        await db.commit()
        await invalidate_article_cache(redis, article.id)
        return {"message": "Статья успешно удалена"}

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка удаления статьи"
        )

@router.get("/{article_id}/history", response_model=List[ArticleHistoryResponse])
async def get_article_history(
    article_id: int,
    offset: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis: aioredis.Redis = Depends(get_redis)
):
    """Получение истории статьи с кэшированием"""
    cache_key = f"article_history:{article_id}:{offset}:{limit}"
    cached_data = await redis.get(cache_key)
    
    if cached_data:
        return json.loads(cached_data)

    result = await db.execute(
        select(Article)
        .where(Article.id == article_id)
    )
    article = result.scalar_one_or_none()
    
    if not article:
        raise HTTPException(status_code=404, detail="Статья не найдена")
    
    if article.author_id != current_user.user_id and current_user.role_id != 2:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    result = await db.execute(
        select(ArticleHistory)
        .where(ArticleHistory.article_id == article_id)
        .order_by(ArticleHistory.changed_at.desc())
        .offset(offset)
        .limit(limit)
    )
    
    history = result.scalars().all()
    await redis.setex(cache_key, 300, json.dumps([h.__dict__ for h in history]))
    return history