"""Прикладной слой и простые in-memory адаптеры для проката самокатов."""
from datetime import datetime, timedelta

from domain import (
    BatteryLevel, Clock, ClockMovedBackwards, DomainInvariantViolation, EntityNotFound,
    GatewayUnresolved, Money, Ride, RideId, Scooter, ScooterId, UserId,
    TransitionNotAllowed, ScooterRepository, RideRepository, PaymentGateway,
    ScooterLock, TariffController, OperatorNotifier,
)


# In-memory репозитории
class InMemoryScooterRepository:
    def __init__(self):
        self.items = {}

    def get(self, scooter_id: ScooterId) -> Scooter:
        if scooter_id.value not in self.items:
            raise EntityNotFound("Самокат не найден")
        return self.items[scooter_id.value]

    def save(self, scooter: Scooter):
        self.items[scooter.id.value] = scooter

    def all(self):
        return list(self.items.values())


class InMemoryRideRepository:
    def __init__(self):
        self.items = {}

    def get(self, ride_id: RideId) -> Ride:
        if ride_id.value not in self.items:
            raise EntityNotFound("Поездка не найдена")
        return self.items[ride_id.value]

    def save(self, ride: Ride):
        self.items[ride.id.value] = ride

    def all(self):
        return list(self.items.values())


# Заглушки внешних систем в оперативной памяти
class InMemoryPaymentGateway:
    def __init__(self):
        self.preauthorization_result = True
        self.charge_result = True
        self.authorized = []
        self.charges = []

    def preauthorize(self, user_id, amount):
        if self.preauthorization_result:
            self.authorized.append((user_id.value, amount.amount))
        return self.preauthorization_result

    def charge(self, user_id, amount):
        if self.charge_result:
            self.charges.append((user_id.value, amount.amount))
        return self.charge_result


class InMemoryScooterLock:
    def __init__(self):
        self.unlock_result = True
        self.unlocked = set()

    def unlock(self, scooter_id):
        if self.unlock_result:
            self.unlocked.add(scooter_id.value)
        return self.unlock_result

    def lock(self, scooter_id):
        self.unlocked.discard(scooter_id.value)


class InMemoryTariffController:
    def __init__(self):
        self.start_result = True
        self.running = set()
        self.paused = set()

    def start(self, scooter_id, rate):
        if self.start_result:
            self.running.add(scooter_id.value)
        return self.start_result

    def pause(self, scooter_id):
        self.paused.add(scooter_id.value)

    def resume(self, scooter_id):
        self.paused.discard(scooter_id.value)

    def stop(self, scooter_id):
        self.running.discard(scooter_id.value)
        self.paused.discard(scooter_id.value)


class InMemoryOperatorNotifier:
    def __init__(self):
        self.notifications = []

    def notify_long_trip(self, ride_id, user_id):
        self.notifications.append((ride_id.value, user_id.value))


# Прикладной сервис реализует последовательность бизнес-процесса.
class ScooterRentalProcess:
    def __init__(self, scooters: ScooterRepository, rides: RideRepository,
                 payments: PaymentGateway, lock: ScooterLock,
                 tariff: TariffController, operator: OperatorNotifier, clock: Clock):
        self.scooters = scooters
        self.rides = rides
        self.payments = payments
        self.lock = lock
        self.tariff = tariff
        self.operator = operator
        self.clock = clock
        self.debts = {}
        self.blocked_users = set()

    def reserve_ride(self, ride_id, user_id, scooter_id, rate, minimum_cost):
        if user_id.value in self.blocked_users:
            raise DomainInvariantViolation("Аккаунт заблокирован из-за задолженности")
        scooter = self.scooters.get(scooter_id)
        scooter.reserve()
        ride = Ride(ride_id, user_id, scooter_id, rate, minimum_cost, self.clock.now())
        self.scooters.save(scooter)
        self.rides.save(ride)
        return ride

    @staticmethod
    def resolve_gateway(value, name):
        if not isinstance(value, bool):
            raise GatewayUnresolved(f"Не удалось определить результат шлюза: {name}")
        return value

    def check_client_debt(self, user_id, debt_clear):
        debt_clear = self.resolve_gateway(debt_clear, "проверка задолженности")
        return debt_clear and user_id.value not in self.blocked_users

    def start_ride(self, ride_id, debt_clear=True):
        ride = self.rides.get(ride_id)
        scooter = self.scooters.get(ride.scooter_id)
        now = self.clock.now()
        if ride.state == "reserved" and now >= ride.reserved_at + timedelta(minutes=10):
            self.on_reservation_timeout(ride_id, now)
            raise TransitionNotAllowed("reservation_expired", "start")
        if "start" not in ride.allowed_events:
            raise TransitionNotAllowed(ride.state, "start")
        no_debt = self.check_client_debt(ride.user_id, debt_clear)
        authorized = False
        unlocked = False
        tariff_started = False

        if no_debt:
            authorized = self.resolve_gateway(self.payments.preauthorize(
                ride.user_id, Money(Ride.PREAUTHORIZATION_AMOUNT)
            ), "предварительная авторизация")
            ride.record_event("PaymentPreauthorized" if authorized else "PreauthorizationFailed",
                              self.clock.now(), "Предварительная авторизация 300 рублей")
        if no_debt and authorized:
            # Две параллельные ветви BPMN выполняются последовательно;
            # переход к поездке происходит только после обеих операций.
            unlocked = self.resolve_gateway(self.lock.unlock(ride.scooter_id), "разблокировка замка")
            ride.record_event("ScooterUnlocked" if unlocked else "UnlockFailed", self.clock.now())
            tariff_started = self.resolve_gateway(self.tariff.start(ride.scooter_id, ride.rate), "запуск тарификации")
            ride.record_event("TariffStarted" if tariff_started else "TariffStartFailed", self.clock.now())

        ride.start(self.clock.now(), no_debt, authorized, unlocked, tariff_started)
        if ride.state == "active":
            scooter.start_rental()
        else:
            if unlocked:
                self.lock.lock(ride.scooter_id)
            if tariff_started:
                self.tariff.stop(ride.scooter_id)
            scooter.cancel_reservation()
        self.scooters.save(scooter)
        self.rides.save(ride)
        return ride

    def pause_ride(self, ride_id):
        ride = self.rides.get(ride_id)
        ride.pause(self.clock.now())
        self.tariff.pause(ride.scooter_id)
        self.rides.save(ride)
        return ride

    def resume_ride(self, ride_id):
        ride = self.rides.get(ride_id)
        ride.resume(self.clock.now())
        self.tariff.resume(ride.scooter_id)
        self.rides.save(ride)
        return ride

    def finish_ride(self, ride_id, parking_allowed, photo_provided, battery_after):
        ride = self.rides.get(ride_id)
        scooter = self.scooters.get(ride.scooter_id)
        parking_allowed = self.resolve_gateway(parking_allowed, "проверка разрешённой зоны")
        photo_provided = self.resolve_gateway(photo_provided, "проверка фотографии")
        cost = ride.finish(self.clock.now(), parking_allowed, photo_provided)
        self.tariff.stop(ride.scooter_id)
        scooter.finish_rental(battery_after)
        self.scooters.save(scooter)

        payment_ok = self.resolve_gateway(self.payments.charge(ride.user_id, cost), "списание оплаты")
        if payment_ok:
            ride.payment_succeeded(self.clock.now())
        else:
            ride.payment_failed(self.clock.now())
            self.debts[ride.user_id.value] = self.debts.get(ride.user_id.value, 0) + cost.amount
            self.blocked_users.add(ride.user_id.value)
        self.rides.save(ride)
        return cost

    def pay_debt(self, user_id):
        amount = self.debts.get(user_id.value, 0)
        if amount <= 0:
            raise DomainInvariantViolation("У пользователя нет задолженности")
        payment_ok = self.resolve_gateway(self.payments.charge(user_id, Money(amount)), "погашение задолженности")
        if not payment_ok:
            raise DomainInvariantViolation("Не удалось погасить задолженность")
        del self.debts[user_id.value]
        self.blocked_users.discard(user_id.value)
        return Money(amount)

    def on_reservation_timeout(self, ride_id: RideId, now: datetime):
        ride = self.rides.get(ride_id)
        scooter = self.scooters.get(ride.scooter_id)
        ride.on_reservation_timeout(now)
        scooter.cancel_reservation()
        self.scooters.save(scooter)
        self.rides.save(ride)
        return ride

    def on_long_trip_timeout(self, ride_id: RideId, now: datetime):
        ride = self.rides.get(ride_id)
        if ride.on_long_trip_timeout(now):
            self.operator.notify_long_trip(ride.id, ride.user_id)
            self.rides.save(ride)
        return ride

    def tick(self, now: datetime):
        changed = []
        for ride in self.rides.all():
            if ride.state == "reserved" and now >= ride.reserved_at + timedelta(minutes=10):
                self.on_reservation_timeout(ride.id, now)
                changed.append(ride.id.value)
            elif ride.state in ("active", "paused") and ride.started_at is not None:
                if now >= ride.started_at + timedelta(hours=12) and not ride.operator_notified:
                    self.on_long_trip_timeout(ride.id, now)
                    changed.append(ride.id.value)
        return changed
