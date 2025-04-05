from fastapi import FastAPI
from src.auth.routes import router as auth_router
from src.user.routes import router as user_router
from src.article.routes import router as article_router
from src.task.routes import router as task_router
from src.admin.routes import router as admin_router
from src.db.database import engine, startup as db_startup
from src.db.models import Role, User
from sqlalchemy.future import select
from src.auth.routes import hash_password
from src.core.config import settings
import logging
import asyncio

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

app = FastAPI(title=settings.PROJECT_NAME, version=settings.PROJECT_VERSION)

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(article_router)
app.include_router(task_router)
app.include_router(admin_router)

async def wait_for_db(max_attempts=10, delay=2):
    attempt = 1
    while attempt <= max_attempts:
        try:
            async with engine.connect() as conn:
                await conn.execute(select(1))
            logger.info("База данных доступна")
            return
        except Exception as e:
            logger.warning(f"Попытка {attempt}/{max_attempts} подключения к базе данных не удалась: {e}")
            if attempt == max_attempts:
                raise Exception("Не удалось подключиться к базе данных после всех попыток")
            await asyncio.sleep(delay)
        attempt += 1

@app.on_event("startup")
async def startup():
    logger.info("Запуск приложения начат")
    try:
        await wait_for_db()

        async with engine.begin() as conn:
            result = await conn.execute(select(Role))
            roles = result.fetchall()
            if not roles:
                await conn.execute(
                    Role.__table__.insert().values([
                        {"role_id": 1, "role_name": "пользователь"},
                        {"role_id": 2, "role_name": "администратор"}
                    ])
                )
                logger.info("Роли по умолчанию успешно созданы")
            else:
                logger.info("Роли уже существуют")

            result = await conn.execute(select(User).where(User.email == "admin@example.com"))
            admin_user = result.scalar_one_or_none()
            if not admin_user:
                hashed_password = hash_password("string111")
                await conn.execute(
                    User.__table__.insert().values({
                        "username": "admin",
                        "full_name": "Админ Админов",
                        "email": "admin@example.com",
                        "hashed_password": hashed_password,
                        "role_id": 2
                    })
                )
                logger.info("Стандартный администратор успешно создан")
            else:
                logger.info("Стандартный администратор уже существует")

        await db_startup()
        logger.info("Приложение успешно запущено")
    except Exception as e:
        logger.error(f"Ошибка при запуске приложения: {e}")
        raise

@app.on_event("shutdown")
async def shutdown():
    logger.info("Завершение работы приложения начато")
    try:
        await engine.dispose()
        logger.info("Соединение с базой данных закрыто")
    except Exception as e:
        logger.error(f"Ошибка при завершении работы приложения: {e}")
        raise
    finally:
        logger.info("Приложение полностью остановлено")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")