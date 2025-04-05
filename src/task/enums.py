from enum import Enum

class TaskStatus(str, Enum):
    ACTIVE = "Текущие"
    POSTPONED = "Отложенные"
    COMPLETED = "Выполненные"

class TaskPriority(str, Enum):
    LOW = "Низкий"
    MEDIUM = "Средний"
    HIGH = "Высокий"
