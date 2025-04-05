from typing import Optional
from sqlalchemy import Column, DateTime, Integer, String, Boolean, ForeignKey, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from src.db.database import Base
from datetime import datetime
from sqlalchemy import Enum as SAEnum
from src.task.enums import TaskPriority, TaskStatus
# Пользователи
class User(Base):
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    full_name = Column(String(100), nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role_id = Column(Integer, ForeignKey("roles.role_id"), default=1)
    registered_at = Column(DateTime, default=func.now())
    avatar_url = Column(String(255), nullable=True)
    completed_tasks_count = Column(Integer, default=0, nullable=False)
    total_tasks_count = Column(Integer, default=0, nullable=False)
    edited_articles_count = Column(Integer, default=0, nullable=False)
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime, nullable=True)
    shift = Column(String(50), nullable=False, comment="Текущая смена пользователя")

# Роли
class Role(Base):
    __tablename__ = "roles"
    role_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    role_name: Mapped[str] = mapped_column(String(50), nullable=False)

#Статьи 
class Article(Base):
    __tablename__ = "articles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(String(5000), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP,
        default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP, nullable=True)
    images = relationship("ArticleImage", back_populates="article", lazy="selectin")
    history = relationship("ArticleHistory", back_populates="article")

class ArticleImage(Base):
    __tablename__ = "article_images"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), nullable=False)
    image_path: Mapped[str] = mapped_column(String(255), nullable=False)
    article = relationship("Article", back_populates="images")

class ArticleHistory(Base):
    __tablename__ = "article_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    event: Mapped[str] = mapped_column(String(50))
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now())
    old_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    new_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    old_content: Mapped[Optional[str]] = mapped_column(String(5000), nullable=True)
    new_content: Mapped[Optional[str]] = mapped_column(String(5000), nullable=True)
    article = relationship("Article", back_populates="history")
# Задачи
class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(5000), nullable=True)
    status: Mapped[TaskStatus] = mapped_column(
        SAEnum(TaskStatus, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=TaskStatus.ACTIVE
    )
    priority: Mapped[TaskPriority] = mapped_column(
        SAEnum(TaskPriority, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=TaskPriority.MEDIUM
    )
    due_date: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now())
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime] = mapped_column(TIMESTAMP, nullable=True)

class TaskHistory(Base):
    __tablename__ = "task_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    event: Mapped[str] = mapped_column(String(50))
    changed_at: Mapped[datetime] = mapped_column(TIMESTAMP, default=func.now())

