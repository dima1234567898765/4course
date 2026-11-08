#!/bin/sh
set -eu
BASE="${BASE_URL:-http://localhost:8000}"

printf '\n1. Проверка готовности приложения (ожидается 200)\n'
curl -i "$BASE/health"

printf '\n2. Создание самоката и брони\n'
curl -i -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"id":"SC-01","battery":85}'
curl -i -X POST "$BASE/rides" -H 'Content-Type: application/json' -d '{"id":"R-01","user_id":"U-01","scooter_id":"SC-01","rate":5,"minimum_cost":30}'

printf '\n3. Основной путь: запуск, пауза, возобновление, завершение и оплата\n'
printf '\nНедопустимое событие в состоянии reserved (ожидается 409), затем проверка, что состояние не изменилось\n'
curl -i -X POST "$BASE/rides/R-01/pause"
curl -i "$BASE/rides/R-01"

curl -i -X POST "$BASE/rides/R-01/start" -H 'Content-Type: application/json' -d '{}'
printf '\nНедопустимое событие в состоянии active (ожидается 409), затем проверка состояния\n'
curl -i -X POST "$BASE/rides/R-01/resume"
curl -i "$BASE/rides/R-01"

curl -i -X POST "$BASE/rides/R-01/pause"
curl -i -X POST "$BASE/rides/R-01/resume"
curl -i -X POST "$BASE/rides/R-01/finish" -H 'Content-Type: application/json' -d '{"parking_allowed":true,"photo_provided":true,"battery_after":70,"payment_success":true}'
curl -i "$BASE/rides/R-01"

printf '\nНедопустимое событие в состоянии paid (ожидается 409), затем проверка состояния\n'
curl -i -X POST "$BASE/rides/R-01/pause"
curl -i "$BASE/rides/R-01"

printf '\n5. Некорректный заряд (ожидается 422)\n'
curl -i -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"id":"SC-BAD","battery":150}'

printf '\n6. Несуществующая поездка (ожидается 404)\n'
curl -i "$BASE/rides/NO-SUCH-RIDE"

printf '\n7. Альтернативный путь: бронь истекает через 10 минут\n'
curl -i -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"id":"SC-02","battery":90}'
curl -i -X POST "$BASE/rides" -H 'Content-Type: application/json' -d '{"id":"R-02","user_id":"U-02","scooter_id":"SC-02","rate":5,"minimum_cost":30}'
RESERVATION_TIME=$(date -u -d '+11 minutes' '+%Y-%m-%dT%H:%M:%S')
curl -i -X POST "$BASE/internal/tick" -H 'Content-Type: application/json' -d "{\"now\":\"$RESERVATION_TIME\"}"
curl -i "$BASE/rides/R-02"

printf '\n8. Альтернативный путь: поездка более 12 часов, уведомление оператора\n'
curl -i -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"id":"SC-03","battery":95}'
curl -i -X POST "$BASE/rides" -H 'Content-Type: application/json' -d '{"id":"R-03","user_id":"U-03","scooter_id":"SC-03","rate":5,"minimum_cost":30}'
curl -i -X POST "$BASE/rides/R-03/start" -H 'Content-Type: application/json' -d '{}'
LONG_TRIP_TIME=$(date -u -d '+13 hours' '+%Y-%m-%dT%H:%M:%S')
curl -i -X POST "$BASE/internal/tick" -H 'Content-Type: application/json' -d "{\"now\":\"$LONG_TRIP_TIME\"}"
curl -i "$BASE/rides/R-03"

printf '\n9. Альтернативный путь: списание не прошло, создан долг и заблокирован аккаунт\n'
curl -i -X POST "$BASE/scooters" -H 'Content-Type: application/json' -d '{"id":"SC-04","battery":90}'
curl -i -X POST "$BASE/rides" -H 'Content-Type: application/json' -d '{"id":"R-04","user_id":"U-04","scooter_id":"SC-04","rate":5,"minimum_cost":30}'
curl -i -X POST "$BASE/rides/R-04/start" -H 'Content-Type: application/json' -d '{}'
curl -i -X POST "$BASE/rides/R-04/finish" -H 'Content-Type: application/json' -d '{"parking_allowed":true,"photo_provided":true,"battery_after":65,"payment_success":false}'
curl -i -X POST "$BASE/users/U-04/pay-debt" -H 'Content-Type: application/json' -d '{"payment_success":true}'
