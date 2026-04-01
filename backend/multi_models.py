"""Pydantic models for the multi-bookie API."""
from pydantic import BaseModel
from typing import Optional


class BookieAccountCreate(BaseModel):
    initials: str
    owner_name: str
    platform: str  # betmakers | amused | tab
    brand: str
    email: str
    password: str
    proxy_base: str = ""
    brand_config: dict = {}
    account_number: Optional[str] = None


class BookieAccountUpdate(BaseModel):
    initials: Optional[str] = None
    owner_name: Optional[str] = None
    platform: Optional[str] = None
    brand: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    proxy_base: Optional[str] = None
    brand_config: Optional[dict] = None
    account_number: Optional[str] = None


class CsvUploadResponse(BaseModel):
    batch_id: str
    total_rows: int
    supported: int
    unsupported: int
    missing_accounts: list[str]
    preview: list[dict]


class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str
    summary: dict
    rows: list[dict]
