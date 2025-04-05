import os
import uuid
from datetime import datetime, timedelta
from typing import List, Optional
import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.auth.auth import get_current_user
from src.db.models import User, Article, ArticleHistory, ArticleImage
from src.db.database import get_db
from src.core.config import settings
from src.article.schemas import ArticleResponse, ArticleHistoryResponse

router = APIRouter(prefix="/articles", tags=["articles"])

# Общая функция для сохранения изображений
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
    current_user: User = Depends(get_current_user)
):
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
    return result.scalars().all()

@router.post("/", response_model=ArticleResponse, status_code=status.HTTP_201_CREATED)
async def create_article(
    title: str = Form(..., min_length=3, max_length=200),
    content: str = Form(...),
    images: List[UploadFile] = File([]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # Создаем статью
        article = Article(
            title=title,
            content=content,
            author_id=current_user.user_id
        )
        db.add(article)
        await db.commit()
        await db.refresh(article)

        # Сохранение изображений
        if images:
            os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
            for image in images:
                file_path = await save_uploaded_file(image, article.id, settings.UPLOAD_DIR)
                db.add(ArticleImage(article_id=article.id, image_path=file_path))
            
            await db.commit()
            await db.refresh(article)

        # Запись в историю
        history_entry = ArticleHistory(
            article_id=article.id,
            user_id=current_user.user_id,
            event="CREATE",
            old_title=None,
            old_content=None,
            new_title=title,
            new_content=content
        )
        db.add(history_entry)
        await db.commit()

        return article

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при создании статьи: {str(e)}"
        )

@router.put("/{article_id}", response_model=ArticleResponse)
async def update_article(
    article_id: int,
    title: Optional[str] = Form(None, min_length=3, max_length=200),
    content: Optional[str] = Form(None),
    images: List[UploadFile] = File([]),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
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

        # Фиксируем изменения для истории
        changes = {}
        if title is not None and title != article.title:
            changes["title"] = {"old": article.title, "new": title}
            article.title = title
        
        if content is not None and content != article.content:
            changes["content"] = {"old": article.content, "new": content}
            article.content = content

        # Сохранение изображений
        if images:
            os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
            for image in images:
                file_path = await save_uploaded_file(image, article.id, settings.UPLOAD_DIR)
                db.add(ArticleImage(article_id=article.id, image_path=file_path))

        # Запись в историю если есть изменения
        if changes:
            history_entry = ArticleHistory(
                article_id=article.id,
                user_id=current_user.user_id,
                event="UPDATE",
                changes=changes
            )
            db.add(history_entry)

        await db.commit()
        await db.refresh(article)
        return article

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при обновлении статьи: {str(e)}"
        )

@router.delete("/{article_id}", response_model=dict)
async def delete_article(
    article_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
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

        article.is_deleted = True
        article.deleted_at = datetime.utcnow()
        
        # Запись в историю
        history_entry = ArticleHistory(
            article_id=article.id,
            user_id=current_user.user_id,
            event="DELETE",
            changes={"status": "deleted"}
        )
        db.add(history_entry)
        
        await db.commit()
        return {"message": "Статья успешно удалена"}

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при удалении статьи: {str(e)}"
        )

@router.post("/{article_id}/restore", response_model=ArticleResponse)
async def restore_article(
    article_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        result = await db.execute(
            select(Article)
            .where(
                Article.id == article_id,
                Article.is_deleted == True,
                Article.deleted_at >= datetime.utcnow() - timedelta(days=7)
            )
        )
        article = result.scalar_one_or_none()
        
        if not article:
            raise HTTPException(
                status_code=404,
                detail="Статья не найдена или срок восстановления истек"
            )
        
        if article.author_id != current_user.user_id and current_user.role_id != 2:
            raise HTTPException(status_code=403, detail="Доступ запрещен")

        article.is_deleted = False
        article.deleted_at = None
        
        # Запись в историю
        history_entry = ArticleHistory(
            article_id=article.id,
            user_id=current_user.user_id,
            event="RESTORE",
            changes={"status": "restored"}
        )
        db.add(history_entry)
        
        await db.commit()
        await db.refresh(article)
        return article

    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при восстановлении статьи: {str(e)}"
        )

@router.get("/{article_id}/history", response_model=List[ArticleHistoryResponse])
async def get_article_history(
    article_id: int,
    offset: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Проверка прав доступа к статье
    result = await db.execute(
        select(Article)
        .where(Article.id == article_id)
    )
    article = result.scalar_one_or_none()
    
    if not article:
        raise HTTPException(status_code=404, detail="Статья не найдена")
    
    if article.author_id != current_user.user_id and current_user.role_id != 2:
        raise HTTPException(status_code=403, detail="Доступ запрещен")

    # Получение истории
    result = await db.execute(
        select(ArticleHistory)
        .where(ArticleHistory.article_id == article_id)
        .order_by(ArticleHistory.changed_at.desc())
        .offset(offset)
        .limit(limit)
    )
    
    return result.scalars().all()