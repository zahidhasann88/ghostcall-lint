"""Valid pydantic 2.10 + FastAPI 0.115 code."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class Priority(StrEnum):
    LOW = "low"
    HIGH = "high"


class Item(BaseModel):
    """Field(): genuinely accepts default, alias, description, ge/le, examples."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    name: str = Field(default="", alias="itemName", min_length=1, max_length=64, description="name")
    priority: Priority = Field(default=Priority.LOW, examples=["low", "high"])
    count: int = Field(default=0, ge=0, le=1000, json_schema_extra={"unit": "items"})
    tags: list[str] = Field(default_factory=list, max_length=10)
    created_at: datetime | None = None
    kind: Literal["a", "b"] = "a"

    @field_validator("name", mode="after")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _check(self) -> "Item":
        if self.count < 0:
            raise ValueError("count must be positive")
        return self


class ItemPage(BaseModel):
    items: list[Item] = []
    total: int = 0


def load(payload: dict[str, Any]) -> Item:
    try:
        return Item.model_validate(payload, strict=False, from_attributes=True)
    except ValidationError as error:
        raise ValueError(str(error)) from error


app = FastAPI(title="clean", version="1.0.0", debug=False)


def get_session() -> dict[str, str]:
    return {"token": "x"}


@app.get("/items/{item_id}", status_code=status.HTTP_200_OK, response_model=Item)
async def read_item(
    item_id: int,
    session: Annotated[dict[str, str], Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100, description="page size")] = 20,
) -> Item:
    del session
    if item_id < 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="missing", headers={"X-Error": "1"})
    return Item(name=str(item_id), count=limit)


@app.post("/items", status_code=status.HTTP_201_CREATED)
async def create_item(item: Item) -> JSONResponse:
    dumped = item.model_dump(mode="json", exclude_none=True, by_alias=True)
    return JSONResponse(content=dumped, status_code=201)


@app.get("/items")
async def list_items(offset: int = Query(0, ge=0), page: ItemPage = ItemPage()) -> dict[str, Any]:
    return {"offset": offset, "items": [entry.model_dump() for entry in page.items]}
