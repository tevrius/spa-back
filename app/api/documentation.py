"""OpenAPI descriptions and examples; no changes to business behaviour."""

from app.api.schemas import ErrorResponse

DESCRIPTION = """
REST API складского учёта и планирования закупок для спа-оператора.

## Правила работы
- Авторизация в локальном MVP не требуется. Не публикуйте сервис в интернет без неё.
- Даты — `YYYY-MM-DD`, бизнес-дата определяется `APP_TIMEZONE` (Europe/Moscow).
  В примерах даты фиксированы: перед отправкой замените дату движения на сегодня
  или прошлую дату, а срок новой партии — на подходящую будущую дату.
- Количества: до 3 знаков после запятой, цена: до 2, валюта: RUB.
  Основные поля ответов — JSON-числа; точные промежуточные значения и ошибки
  могут содержать десятичные строки или рациональные числа вида `1/90`.
- `current_stock` включает ещё не списанную просрочку; `available_stock` её исключает.
- Расход — FEFO: ближайшая годность, дата первого прихода, ID партии.
  Товар пригоден по дату годности включительно.
- Номер документа глобально уникален. Повторная отправка возвращает 409,
  а не результат первого запроса. Запись атомарна.
- Списки возвращают `items`, `limit`, `offset`, `total`; total — число после фильтрации,
  до пагинации. Результат одного запроса читается из согласованного снимка БД.

## Быстрая проверка
1. `GET /api/stock` — демоостатки OIL-001 / MS-01.
2. `POST /api/movements` — приход с новым document_number, затем расход.
3. `GET /api/stock/OIL-001` — партии и приходные документы.
4. `POST /api/forecast` — горизонт days **или** months.
5. `GET /api/alerts` — риски и показатели, на которых они основаны.

Swagger: `/docs`, ReDoc: `/redoc`, машинная спецификация OpenAPI 3.1: `/openapi.json`.
"""

TAGS = [
    {"name": "movements", "description": "Регистрация и история складских движений."},
    {"name": "stock", "description": "Вычисляемые остатки по объектам и партиям."},
    {"name": "forecast", "description": "Детерминированный прогноз и объяснение закупки."},
    {"name": "alerts", "description": "Дефицит, сроки годности и отсутствие движения."},
    {"name": "health", "description": "Проверка процесса и готовности базы данных."},
]

MOVEMENT_EXAMPLES = {
    "receipt": {
        "summary": "Приход в новую партию",
        "value": {
            "date": "2026-09-23",
            "sku": "OIL-001",
            "location": "MS-01",
            "operation": "receipt",
            "quantity": 10,
            "batch": "MANUAL-BATCH-001",
            "document_number": "MANUAL-RECEIPT-001",
            "expires_on": "2027-09-23",
            "unit_price": 1500,
        },
    },
    "consume": {
        "summary": "Расход с автоматическим FEFO",
        "value": {
            "date": "2026-09-23",
            "sku": "OIL-001",
            "location": "MS-01",
            "operation": "consume",
            "quantity": 3,
            "document_number": "MANUAL-CONSUME-001",
        },
    },
    "return": {
        "summary": "Возврат материала на склад",
        "value": {
            "date": "2026-09-23",
            "sku": "OIL-001",
            "location": "MS-01",
            "operation": "return",
            "quantity": 1,
            "batch": "MANUAL-BATCH-001",
            "document_number": "MANUAL-RETURN-001",
        },
    },
    "writeoff": {
        "summary": "Списание конкретной партии",
        "value": {
            "date": "2026-09-23",
            "sku": "OIL-001",
            "location": "MS-01",
            "operation": "writeoff",
            "quantity": 2,
            "batch": "MANUAL-BATCH-001",
            "document_number": "MANUAL-WRITEOFF-001",
        },
    },
    "correction": {
        "summary": "Отрицательная корректировка",
        "value": {
            "date": "2026-09-23",
            "sku": "OIL-001",
            "location": "MS-01",
            "operation": "correction",
            "quantity": -1,
            "batch": "MANUAL-BATCH-001",
            "document_number": "MANUAL-CORRECTION-001",
        },
    },
}

FORECAST_EXAMPLES = {
    "days": {
        "summary": "На 30 дней со страховым запасом 14 дней",
        "value": {
            "sku": "OIL-001",
            "location": "MS-01",
            "days": 30,
            "safety_stock_days": 14,
        },
    },
    "months": {
        "summary": "На три календарных месяца",
        "value": {
            "sku": "OIL-001",
            "location": "MS-01",
            "months": 3,
            "safety_stock_days": 14,
        },
    },
    "deferred": {
        "summary": "Отложенное начало периода",
        "value": {
            "sku": "OIL-001",
            "location": "MS-01",
            "months": 3,
            "start_date": "2026-10-01",
            "safety_stock_days": 14,
        },
    },
}

ERROR_EXAMPLES = {
    400: (
        "Некорректный синтаксис JSON",
        "invalid_json",
        "Malformed JSON",
        {
            "errors": [
                {"location": ["body", 1], "message": "JSON decode error", "type": "json_invalid"}
            ],
        },
    ),
    404: (
        "SKU, объект или запрошенная партия не найдены",
        "unknown_sku",
        "SKU does not exist",
        {
            "sku": "UNKNOWN",
        },
    ),
    409: (
        "Номер документа уже зарегистрирован",
        "duplicate_document",
        "Document number already exists",
        {},
    ),
    422: (
        "Невалидные поля или нарушение бизнес-правил",
        "validation_error",
        "Request validation failed",
        {
            "errors": [
                {
                    "location": ["body", "quantity"],
                    "message": "Invalid quantity",
                    "type": "value_error",
                }
            ],
        },
    ),
    503: (
        "База временно недоступна",
        "database_unavailable",
        "Database temporarily unavailable",
        {},
    ),
}


def error_responses(*statuses: int, insufficient_stock: bool = False) -> dict:
    responses = {}
    for status in statuses:
        description, code, message, details = ERROR_EXAMPLES[status]
        examples = {
            "default": {
                "summary": description,
                "value": {
                    "error": {"code": code, "message": message, "details": details},
                },
            }
        }
        if status == 422 and insufficient_stock:
            examples["insufficient_stock"] = {
                "summary": "Недостаточно пригодного запаса",
                "value": {
                    "error": {
                        "code": "insufficient_stock",
                        "message": "Consumption exceeds usable stock",
                        "details": {
                            "requested": "12",
                            "available": "10",
                            "operation_date": "2026-09-23",
                        },
                    },
                },
            }
        responses[status] = {
            "model": ErrorResponse,
            "description": description,
            "content": {"application/json": {"examples": examples}},
        }
    return responses


MOVEMENT_RESPONSE = {
    "id": 19,
    "date": "2026-09-23",
    "sku": "OIL-001",
    "location": "MS-01",
    "operation": "receipt",
    "quantity": 10,
    "document_number": "MANUAL-RECEIPT-001",
    "allocations": [{"batch": "MANUAL-BATCH-001", "quantity_delta": 10}],
}
STOCK_RESPONSE = {
    "sku": "OIL-001",
    "name": "Массажное масло",
    "unit": "л",
    "location": "MS-01",
    "current_stock": 10,
    "available_stock": 10,
    "expired_stock": 0,
    "avg_daily_consumption": 1,
    "days_of_stock": 10,
    "nearest_expiry": "2027-09-23",
}
FORECAST_RESPONSE = {
    "sku": "OIL-001",
    "name": "Массажное масло",
    "unit": "л",
    "location": "MS-01",
    "period": {"from": "2026-09-23", "to": "2026-10-22", "days": 30},
    "avg_daily_consumption": 1,
    "forecast_demand": 30,
    "current_stock": 10,
    "available_stock": 10,
    "incoming_qty": 0,
    "safety_stock": 14,
    "reorder_point": 21,
    "recommended_purchase_qty": 35,
    "unit_price": 12.55,
    "estimated_cost": 439.25,
    "recommended_order_date": "2026-09-23",
    "stockout_date": "2026-10-03",
    "explanation": {
        "as_of": "2026-09-23",
        "data_used": ["Расход 90 единиц за 90 полных дней; запас 10"],
        "formulas": [
            "D = 90 / 90 = 1",
            "ROP = 1 × (7 + 14) = 21",
            "Qraw = 30 + 14 − 10 = 34",
            "Q = ceil(max(34, 10) / 5) × 5 = 35",
        ],
        "assumptions": ["Постоянный спрос; цена последнего поступления; валюта RUB"],
        "inputs": {
            "history_consumption": "90",
            "daily_rate_exact": "1",
            "pack_size": "5",
            "minimum_order": "10",
            "lead_time_days": 7,
            "required_before_rounding": "34",
        },
    },
    "warnings": [
        {"code": "below_reorder_point", "level": "warning", "message": "Остаток ниже точки заказа"},
        {
            "code": "projected_shortage",
            "level": "critical",
            "message": "Без закупки возникнет дефицит",
        },
    ],
}
ALERT_RESPONSE = {
    "code": "expired_batch",
    "level": "critical",
    "message": "Партия просрочена: требуется writeoff",
    "sku": "MASK-001",
    "location": "MS-01",
    "batch": "MASK-EXPIRED",
    "metrics": {
        "remaining_quantity": "4.000",
        "expires_on": "2026-09-22",
        "days_to_expiry": -1,
        "threshold_days": 30,
    },
}


def success_response(description: str, example: dict) -> dict:
    return {"description": description, "content": {"application/json": {"example": example}}}
