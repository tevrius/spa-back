from datetime import date as Date
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
    date: Date = Field(description="Дата операции YYYY-MM-DD, не позже бизнес-даты в APP_TIMEZONE.")
    sku: Code = Field(description="Уникальный артикул товара, например OIL-001.")
    location: Code = Field(
        description=(
            "Код объекта хранения, например MS-01. null у предупреждения без привязки к объекту."
        )
    )
    operation: Operation = Field(
        description=(
            "receipt — приход; consume — FEFO-расход; return — возврат на склад; "
            "writeoff — списание; correction — знаковое изменение."
        )
    )
    quantity: Quantity = Field(
        description=(
            "Количество в единице товара, до 3 знаков после запятой. Ненулевое; "
            "отрицательное только у correction."
        )
    )
    batch: Annotated[str, Field(min_length=1, max_length=128)] | None = Field(
        default=None,
        description="Обязательна кроме consume; consume выбирает партии автоматически.",
    )
    document_number: Annotated[str, Field(min_length=1, max_length=128)] = Field(
        description="Глобально уникальный номер документа. Повторная загрузка — 409."
    )
    expires_on: Date | None = Field(
        default=None, description="Обязательна для новой партии receipt; годность включительно."
    )
    unit_price: Price | None = Field(
        default=None, description="Цена в RUB, обязательна для новой партии receipt."
    )

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
    batch: str = Field(description="Код партии в рамках SKU и объекта.")
    quantity_delta: Number = Field(
        description="Знаковое изменение остатка партии: расход отрицательный, приход положительный."
    )


class MovementOut(BaseModel):
    id: int = Field(description="Идентификатор зарегистрированного движения.")
    date: Date = Field(description="Дата операции YYYY-MM-DD, не позже бизнес-даты в APP_TIMEZONE.")
    sku: str = Field(description="Уникальный артикул товара, например OIL-001.")
    location: str = Field(
        description=(
            "Код объекта хранения, например MS-01. null у предупреждения без привязки к объекту."
        )
    )
    operation: Operation = Field(
        description=(
            "receipt — приход; consume — FEFO-расход; return — возврат на склад; "
            "writeoff — списание; correction — знаковое изменение."
        )
    )
    quantity: Number = Field(
        description=(
            "Количество в единице товара, до 3 знаков после запятой. Ненулевое; "
            "отрицательное только у correction."
        )
    )
    document_number: str = Field(
        description=(
            "Глобально уникальный номер документа одного движения. Повторная загрузка — 409."
        )
    )
    allocations: list[AllocationOut] = Field(
        description=(
            "Распределение движения по партиям. Может пересчитаться при операции задним числом."
        )
    )


class MovementCreated(MovementOut):
    current_stock: Number = Field(
        description="Учётный остаток из движений, включая ещё не списанную просрочку."
    )
    available_stock: Number = Field(
        description="Пригодный для расхода остаток, исключая просроченные партии."
    )


class MovementPage(BaseModel):
    items: list[MovementOut] = Field(description="Записи текущей страницы.")
    limit: int = Field(description="Размер страницы (1–100, по умолчанию 50).")
    offset: int = Field(description="Количество пропущенных записей, начиная с 0.")
    total: int = Field(description="Число записей после фильтров, до пагинации.")


class StockOut(BaseModel):
    sku: str = Field(description="Уникальный артикул товара, например OIL-001.")
    name: str = Field(description="Название товара.")
    unit: str = Field(description="Единица измерения количества товара.")
    location: str = Field(
        description=(
            "Код объекта хранения, например MS-01. null у предупреждения без привязки к объекту."
        )
    )
    current_stock: Number = Field(
        description="Учётный остаток из движений, включая ещё не списанную просрочку."
    )
    available_stock: Number = Field(
        description="Пригодный для расхода остаток, исключая просроченные партии."
    )
    expired_stock: Number = Field(
        description="Просроченный остаток, требующий отдельной операции writeoff."
    )
    avg_daily_consumption: Number = Field(
        description="Расход consume за последние 90 полных дней / 90, показан до 0.001."
    )
    days_of_stock: Number | None = Field(
        description="Доступный остаток / средний расход; null при нулевом расходе."
    )
    nearest_expiry: Date | None = Field(
        description=(
            "Ближайшая годность партии с положительным остатком, включая "
            "просроченные; null при отсутствии."
        )
    )


class StockPage(BaseModel):
    items: list[StockOut] = Field(description="Записи текущей страницы.")
    limit: int = Field(description="Размер страницы (1–100, по умолчанию 50).")
    offset: int = Field(description="Количество пропущенных записей, начиная с 0.")
    total: int = Field(description="Число записей после фильтров, до пагинации.")


class BatchOut(BaseModel):
    batch: str = Field(description="Код партии в рамках товара и объекта.")
    current_stock: Number = Field(
        description="Учётный остаток из движений, включая ещё не списанную просрочку."
    )
    expires_on: Date = Field(
        description="Срок годности включительно. У новой партии не раньше даты поступления."
    )
    received_on: Date = Field(description="Дата первого поступления партии.")
    unit_price: Number = Field(
        description=(
            "Цена единицы в RUB, до 2 десятичных знаков. В прогнозе null, если "
            "нет цены поступления."
        )
    )
    receipt_documents: list[str] = Field(
        description="Номера приходных документов этой партии в хронологическом порядке."
    )
    expired: bool = Field(description="Истёк ли срок годности на дату запроса.")


class LocationStock(StockOut):
    batches: list[BatchOut] = Field(
        description="Партии объекта, включая партии с нулевым остатком."
    )


class StockDetail(BaseModel):
    sku: str = Field(description="Уникальный артикул товара, например OIL-001.")
    name: str = Field(description="Название товара.")
    unit: str = Field(description="Единица измерения количества товара.")
    locations: list[LocationStock] = Field(
        description="Объекты с партиями товара. Пустой массив, если партий нет."
    )


class ForecastIn(Input):
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "required": ["days"],
                    "properties": {"days": {"type": "integer"}, "months": {"type": "null"}},
                },
                {
                    "required": ["months"],
                    "properties": {"months": {"type": "integer"}, "days": {"type": "null"}},
                },
            ]
        }
    )
    sku: Code = Field(description="Уникальный артикул товара, например OIL-001.")
    location: Code = Field(
        description=(
            "Код объекта хранения, например MS-01. null у предупреждения без привязки к объекту."
        )
    )
    days: Annotated[int, Field(strict=True, ge=1, le=366)] | None = Field(
        default=None, description="Горизонт 1–366 дней. Укажите days или months, но не оба."
    )
    months: Annotated[int, Field(strict=True, ge=1, le=12)] | None = Field(
        default=None, description="Горизонт 1–12 календарных месяцев, альтернативный days."
    )
    safety_stock_days: Annotated[int, Field(strict=True, ge=0, le=366)] = Field(
        default=14, description="Страховой запас в днях среднего расхода."
    )
    start_date: Date | None = Field(
        default=None,
        description="Начало периода: сегодня по умолчанию; не раньше сегодня и не далее 366 дней.",
    )

    @model_validator(mode="after")
    def one_horizon(self) -> "ForecastIn":
        if (self.days is None) == (self.months is None):
            raise ValueError("Specify exactly one horizon: days or months")
        return self


class Period(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_date: Date = Field(alias="from", description="Первый день периода включительно.")
    to: Date = Field(description="Последний день периода включительно.")
    days: int = Field(description="Количество календарных дней периода.")


class WarningOut(BaseModel):
    code: str = Field(description="Стабильный машинный код для обработки клиентом.")
    level: Literal["info", "warning", "critical"] = Field(
        description="Важность: info — информация, warning — внимание, critical — высокий риск."
    )
    message: str = Field(description="Человекочитаемое сообщение.")


class Explanation(BaseModel):
    data_used: list[str] = Field(description="Источники и окно данных, использованные при расчёте.")
    formulas: list[str] = Field(description="Формулы расчёта и округления.")
    assumptions: list[str] = Field(description="Допущения и ограничения модели.")
    as_of: Date = Field(description="Бизнес-дата, на которую выполнен расчёт.")
    inputs: dict[str, object] = Field(
        description=(
            "Исходные показатели и промежуточные значения; точные дроби могут "
            "быть строками вида 1/90."
        )
    )


class ForecastOut(BaseModel):
    sku: str = Field(description="Уникальный артикул товара, например OIL-001.")
    name: str = Field(description="Название товара.")
    unit: str = Field(description="Единица измерения количества товара.")
    location: str = Field(
        description=(
            "Код объекта хранения, например MS-01. null у предупреждения без привязки к объекту."
        )
    )
    period: Period = Field(description="Выбранный календарный период прогноза.")
    avg_daily_consumption: Number = Field(
        description="Расход consume за последние 90 полных дней / 90, показан до 0.001."
    )
    forecast_demand: Number = Field(
        description="Средний расход × дни выбранного периода; не включает промежуток до start_date."
    )
    current_stock: Number = Field(
        description="Учётный остаток из движений, включая ещё не списанную просрочку."
    )
    available_stock: Number = Field(
        description="Пригодный для расхода остаток, исключая просроченные партии."
    )
    incoming_qty: Number = Field(
        description=(
            "Неполученные части открытых поставок от as_of до конца периода, включая до start_date."
        )
    )
    safety_stock: Number = Field(description="Средний расход × safety_stock_days.")
    reorder_point: Number = Field(description="Средний расход × (срок поставки + страховые дни).")
    recommended_purchase_qty: Number = Field(
        description=(
            "Неотрицательная потребность, округлённая вверх до упаковки и минимального заказа."
        )
    )
    unit_price: Number | None = Field(
        description=(
            "Цена единицы в RUB, до 2 десятичных знаков. В прогнозе null, если "
            "нет цены поступления."
        )
    )
    estimated_cost: Number | None = Field(
        description="Закупка × цена, HALF_UP до копеек; null при неизвестной цене."
    )
    recommended_order_date: Date | None = Field(
        description="Рекомендуемая дата заказа, не раньше as_of; null при нулевой закупке."
    )
    stockout_date: Date | None = Field(
        description=(
            "Первый день непокрытого спроса без новой закупки; null, если не "
            "найден до конца горизонта."
        )
    )
    explanation: Explanation = Field(description="Исходные данные, формулы и допущения расчёта.")
    warnings: list[WarningOut] = Field(
        description="Предупреждения о дефиците и ограничениях исходных данных."
    )


class AlertOut(WarningOut):
    sku: str = Field(description="Уникальный артикул товара, например OIL-001.")
    location: str | None = Field(
        description=(
            "Код объекта хранения, например MS-01. null у предупреждения без привязки к объекту."
        )
    )
    batch: str | None = Field(
        default=None, description="Партия для предупреждения о годности; иначе null."
    )
    metrics: dict[str, object] = Field(
        description="Показатели и пороги конкретного предупреждения; набор ключей зависит от code."
    )


class AlertPage(BaseModel):
    items: list[AlertOut] = Field(description="Записи текущей страницы.")
    limit: int = Field(description="Размер страницы (1–100, по умолчанию 50).")
    offset: int = Field(description="Количество пропущенных записей, начиная с 0.")
    total: int = Field(description="Число записей после фильтров, до пагинации.")


class ErrorBody(BaseModel):
    code: str = Field(description="Стабильный машинный код для обработки клиентом.")
    message: str = Field(description="Человекочитаемое сообщение.")
    details: dict[str, object] = Field(
        description="Подробности ошибки: доступное количество, дата или список ошибок валидации."
    )


class ErrorResponse(BaseModel):
    error: ErrorBody = Field(description="Единый объект ошибки API.")


class LiveStatus(BaseModel):
    status: Literal["ok"] = Field(description="Процесс обслуживает HTTP-запросы.")


class ReadyStatus(BaseModel):
    status: Literal["ready"] = Field(description="Доступны БД и таблица версии миграций.")
