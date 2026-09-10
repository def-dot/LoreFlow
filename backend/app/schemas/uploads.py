"""Upload schemas."""

from pydantic import BaseModel


class UploadOut(BaseModel):
    id: str
    filename: str
    size: int
