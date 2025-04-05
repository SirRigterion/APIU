from pydantic import BaseModel, validator, EmailStr
import re

class UserCreate(BaseModel):
    username: str = "user"
    full_name: str = "Иван Иванович Иванов"
    email: EmailStr = "user@example.com"
    password: str = "string111"
    shift: str = "Первая"

    @validator('username')
    def validate_username(cls, value):
        if not re.match(r'^[a-zA-Z]+$', value):
            raise ValueError('Имя пользователя должно содержать только латинские буквы.')
        return value

    @validator('full_name')
    def validate_full_name(cls, value):
        if not re.match(r'^[а-яА-ЯёЁ\s]+$', value):
            raise ValueError('Полное имя должно содержать только русские буквы и пробелы.')
        return value

    @validator('password')
    def validate_password(cls, value):
        if not re.match(r'^[a-zA-Z0-9!@#$%^&*]+$', value):
            raise ValueError('Пароль должен содержать только латинские буквы, цифры и символы (!@#$%^&*)')
        if len(value) < 8:
            raise ValueError('Пароль должен быть длиной не менее 8 символов.')
        return value

class UserLogin(BaseModel):
    username: str = "admin"
    password: str = "string111"