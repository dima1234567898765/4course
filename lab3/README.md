# Лабораторная работа №3 — аренда электросамоката

## Запуск локально

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Сервер использует порт `8000` по умолчанию. Порт можно изменить переменной `PORT`, уровень логирования — `LOG_LEVEL`.

## Запуск в Docker

```bash
docker compose up --build
```

## Проверка HTTP-маршрутов

```bash
./curl_tests.sh
```

## Основные маршруты

- `GET /health` — состояние приложения.
- `POST /scooters` и `GET /scooters/<id>` — создание и получение самоката.
- `POST /rides` — создание бесплатной брони.
- `POST /rides/<id>/start` — проверка клиента, авторизация 300 рублей, замок и тарификация.
- `POST /rides/<id>/pause` — пауза поездки.
- `POST /rides/<id>/resume` — возобновление поездки.
- `POST /rides/<id>/finish` — проверка зоны и фотографии, расчёт и списание стоимости.
- `POST /users/<id>/pay-debt` — погашение задолженности.
- `GET /rides/<id>` — состояние, доступные события и журнал событий.
- `POST /internal/tick` — установка времени приложения вперёд и выполнение таймеров.

Внешние системы представлены in-memory заглушками. В запросах `start` и `finish` можно передать флаги результата заглушки для проверки альтернативных ветвей.

## Запуск автоматических тестов

```bash
pip install -r requirements-dev.txt
pytest -q
```
