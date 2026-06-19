from pydantic import BaseModel
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class FileInfo(BaseModel):
    lang: str
    status: str


class AgentState(BaseModel):
    changed_files: dict[str, FileInfo]

