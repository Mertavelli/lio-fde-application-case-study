import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    article_number: str
    supplier_id: uuid.UUID
    supplier_name: str | None = None
    description: str
    unit_price: Decimal
    currency: str
    unit: str
    quantity: Decimal
    created_at: datetime


class ArticlePage(BaseModel):
    """A page of articles plus the totals the UI needs to render pagination."""

    items: list[ArticleOut]
    total: int
    limit: int
    offset: int


class SemanticSuggestionIn(BaseModel):
    description: str = Field(min_length=3, max_length=300)
    unit: str = Field(min_length=1, max_length=50)
    supplier: str = Field(default="", max_length=200)
    unit_price: float | None = Field(default=None, gt=0, le=1_000_000_000, allow_inf_nan=False)
    amount: float | None = Field(default=None, gt=0, le=1_000_000_000, allow_inf_nan=False)
