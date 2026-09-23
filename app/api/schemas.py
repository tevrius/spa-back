from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=3)]
Price = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]
# Keep the numeric JSON contract from the specification; calculations stay Decimal.
Number = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]
Code = Annotated[str, Field(min_length=1, max_length=64)]
Operation = Literal["receipt", "consume", "writeoff", "return", "correction"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MovementIn(Input):
    date: date
    sku: Code
    location: Code
    operation: Operation
    quantity: Quantity
    batch: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    document_number: Annotated[str, Field(min_length=1, max_length=128)]
    expires_on: date | None = None
    unit_price: Price | None = None

    @model_validator(mode="after")
    def valid_operation(self) -> "MovementIn":
        if self.quantity == 0 or (self.operation != "correction" and self.quantity < 0):
            raise ValueError("Quantity must be positive, except a signed nonzero correction")
        if self.operation == "consume" and self.batch is not None:
            raise ValueError("Consumption batches are selected automatically by FEFO")
        if self.operation != "consume" and self.batch is None:
            raise ValueError("This operation requires a batch code")
        if self.operation != "receipt" and (
            self.expires_on is not None or self.unit_price is not None
        ):
            raise ValueError("Batch metadata is only accepted for receipt")
        return self


class AllocationOut(BaseModel):
    batch: str
    quantity_delta: Number


class MovementOut(BaseModel):
    id: int
    date: date
    sku: str
    location: str
    operation: Operation
    quantity: Number
    document_number: str
    allocations: list[AllocationOut]


class MovementCreated(MovementOut):
    current_stock: Number
    available_stock: Number


class MovementPage(BaseModel):
    items: list[MovementOut]
    limit: int
    offset: int
    total: int


class StockOut(BaseModel):
    sku: str
    name: str
    unit: str
    location: str
    current_stock: Number
    available_stock: Number
    expired_stock: Number
    avg_daily_consumption: Number
    days_of_stock: Number | None
    nearest_expiry: date | None


class StockPage(BaseModel):
    items: list[StockOut]
    limit: int
    offset: int
    total: int


class BatchOut(BaseModel):
    batch: str
    current_stock: Number
    expires_on: date
    received_on: date
    unit_price: Number
    receipt_documents: list[str]
    expired: bool


class LocationStock(StockOut):
    batches: list[BatchOut]


class StockDetail(BaseModel):
    sku: str
    name: str
    unit: str
    locations: list[LocationStock]


class ForecastIn(Input):
    sku: Code
    location: Code
    days: Annotated[int, Field(strict=True, ge=1, le=366)] | None = None
    months: Annotated[int, Field(strict=True, ge=1, le=12)] | None = None
    safety_stock_days: Annotated[int, Field(strict=True, ge=0, le=366)] = 14
    start_date: date | None = None

    @model_validator(mode="after")
    def one_horizon(self) -> "ForecastIn":
        if (self.days is None) == (self.months is None):
            raise ValueError("Specify exactly one horizon: days or months")
        return self


class Period(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_date: date = Field(alias="from")
    to: date
    days: int


class WarningOut(BaseModel):
    code: str
    level: Literal["info", "warning", "critical"]
    message: str


class Explanation(BaseModel):
    data_used: list[str]
    formulas: list[str]
    assumptions: list[str]
    as_of: date
    inputs: dict[str, object]


class ForecastOut(BaseModel):
    sku: str
    name: str
    unit: str
    location: str
    period: Period
    avg_daily_consumption: Number
    forecast_demand: Number
    current_stock: Number
    available_stock: Number
    incoming_qty: Number
    safety_stock: Number
    reorder_point: Number
    recommended_purchase_qty: Number
    unit_price: Number | None
    estimated_cost: Number | None
    recommended_order_date: date | None
    stockout_date: date | None
    explanation: Explanation
    warnings: list[WarningOut]


class AlertOut(WarningOut):
    sku: str
    location: str | None
    batch: str | None = None
    metrics: dict[str, object]


class AlertPage(BaseModel):
    items: list[AlertOut]
    limit: int
    offset: int
    total: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, object]


class ErrorResponse(BaseModel):
    error: ErrorBody
