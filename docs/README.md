# OpenAPI-контракт

`openapi.json` — сгенерированный OpenAPI 3.1.0, пригодный для импорта в Postman,
Insomnia и генераторы клиентов с поддержкой OpenAPI 3.1.
При импорте файла задайте базовый URL `http://localhost:8000` (или свой порт).
В схеме используется относительный сервер `/`, чтобы Swagger работал на текущем адресе.

Источник истины — маршруты FastAPI, Pydantic-схемы и примеры в
`app/api/documentation.py`. JSON не редактируется вручную. Примеры описывают
иллюстративные сценарии, а не гарантированное состояние демобазы.

## Просмотр

После `docker compose up --build`:

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Актуальная схема запущенного сервиса: http://localhost:8000/openapi.json

Swagger позволяет отправлять запросы. Заменяйте фиксированные даты примеров
на подходящие текущие даты и используйте новые номера документов.
Swagger и ReDoc загружают JavaScript/CSS с CDN; для отображения интерфейса
нужен доступ к интернету. JSON-схема доступна локально без CDN.

## Обновление

Из корня репозитория, с активным Python-окружением проекта:

```bash
python -m scripts.export_openapi
python -m scripts.export_openapi --check
```

Экспорт не требует PostgreSQL. Без локального Python можно использовать Docker
(команды для macOS/Linux, выполняются из корня репозитория):

```bash
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" \
  -v "$PWD:/app" api python -m scripts.export_openapi
docker compose run --rm --no-deps -v "$PWD:/app:ro" \
  api python -m scripts.export_openapi --check
```

Для первого использования соберите образ: `docker compose build api`.
Скрипт по умолчанию сохраняет `docs/openapi.json`, `--output` меняет путь.
`--check` не пишет файлы и завершается с кодом 1, если снимок отсутствует
или отличается от схемы приложения.

## Проверки контракта

```bash
docker compose --profile test run --build --rm --no-deps tests \
  pytest tests/unit/test_openapi.py -p no:cacheprovider
```

Тесты проверяют валидность OpenAPI 3.1, совпадение снимка с приложением,
уникальные operationId, примеры ответов по JSON Schema, примеры запросов по
Pydantic, исключительность days/months и единый контракт ошибок.
При изменении API обновите снимок, выполните тесты и включите JSON в коммит.

## Операции

| operationId | Метод и путь | Назначение |
|---|---|---|
| createMovement | POST /api/movements | Создать движение |
| listMovements | GET /api/movements | История с фильтрами |
| listStock | GET /api/stock | Текущие остатки |
| getStockBySku | GET /api/stock/{sku} | Партии и объекты товара |
| calculateForecast | POST /api/forecast | Рассчитать закупку |
| listAlerts | GET /api/alerts | Риски склада |
| getLiveness | GET /health/live | Доступность процесса |
| getReadiness | GET /health/ready | Доступность базы и таблицы миграций |

Документация описывает локальный MVP без авторизации. Введение схемы
аутентификации требует реальной реализации проверки прав, не только OpenAPI security.
