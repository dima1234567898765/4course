"""Доменная модель проката электросамокатов. Только стандартная библиотека Python."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from typing import Protocol


# Доменные исключения
class DomainError(Exception):
    pass


class DomainInvariantViolation(DomainError):
    pass


class InvalidValueObject(DomainError):
    pass


class EntityNotFound(DomainError):
    pass


class TransitionNotAllowed(DomainError):
    def __init__(self, state: str, event: str):
        super().__init__(f"Событие '{event}' недопустимо в состоянии '{state}'")


class ClockMovedBackwards(DomainError):
    pass


class GatewayUnresolved(DomainError):
    pass


# Неизменяемые объекты-значения
@dataclass(frozen=True)
class ScooterId:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("ID самоката не может быть пустым")


@dataclass(frozen=True)
class UserId:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("ID пользователя не может быть пустым")


@dataclass(frozen=True)
class RideId:
    value: str

    def __post_init__(self):
        if not self.value.strip():
            raise InvalidValueObject("ID поездки не может быть пустым")


@dataclass(frozen=True)
class BatteryLevel:
    percent: int

    def __post_init__(self):
        if not isinstance(self.percent, int) or not 0 <= self.percent <= 100:
            raise InvalidValueObject("Заряд должен быть целым числом от 0 до 100")


@dataclass(frozen=True)
class Money:
    amount: float

    def __post_init__(self):
        if self.amount < 0:
            raise InvalidValueObject("Сумма не может быть отрицательной")


@dataclass(frozen=True)
class MinuteRate:
    amount: float

    def __post_init__(self):
        if self.amount < 0:
            raise InvalidValueObject("Тариф не может быть отрицательным")


@dataclass(frozen=True)
class DomainEvent:
    name: str
    occurred_at: datetime
    details: str = ""


# Агрегат самоката 
class Scooter:
    MIN_BATTERY = 20

    def __init__(self, scooter_id: ScooterId, battery: BatteryLevel,
                 status: str = "free"):
        if status not in ("free", "reserved", "rented", "maintenance"):
            raise DomainInvariantViolation("Неизвестное состояние самоката")
        if status in ("reserved", "rented") and battery.percent < self.MIN_BATTERY:
            raise DomainInvariantViolation("Недостаточный заряд для аренды")
        self._id = scooter_id
        self._battery = battery
        self._status = status

    @property
    def id(self):
        return self._id

    @property
    def status(self):
        return self._status

    @property
    def battery(self):
        return self._battery

    def reserve(self):
        if self._status != "free":
            raise DomainInvariantViolation("Самокат не свободен")
        if self._battery.percent < self.MIN_BATTERY:
            raise DomainInvariantViolation("Заряд ниже 20%")
        self._status = "reserved"

    def start_rental(self):
        if self._status != "reserved":
            raise DomainInvariantViolation("Самокат не забронирован")
        self._status = "rented"

    def cancel_reservation(self):
        if self._status != "reserved":
            raise DomainInvariantViolation("Самокат не забронирован")
        self._status = "free"

    def finish_rental(self, battery: BatteryLevel):
        if self._status != "rented":
            raise DomainInvariantViolation("Самокат не арендован")
        self._battery = battery
        self._status = "free"

    def send_to_maintenance(self):
        if self._status in ("reserved", "rented"):
            raise DomainInvariantViolation("Нельзя обслуживать забронированный или арендованный самокат")
        self._status = "maintenance"

    def to_state(self):
        return {"id": self._id.value, "battery": self._battery.percent,
                "status": self._status}

    @classmethod
    def restore(cls, state):
        return cls(ScooterId(state["id"]), BatteryLevel(state["battery"]), state["status"])


# Паттерн «Состояние»: базовый класс запрещает все неописанные переходы
class RideState:
    name = "UNKNOWN"

    def allowed_events(self):
        return []

    def _deny(self, event):
        raise TransitionNotAllowed(self.name, event)

    def start(self, ride, now, debt_clear, payment_authorized, lock_unlocked, tariff_started):
        self._deny("start")

    def on_reservation_timeout(self, ride, now):
        self._deny("on_reservation_timeout")

    def pause(self, ride, now):
        self._deny("pause")

    def resume(self, ride, now):
        self._deny("resume")

    def finish(self, ride, now, parking_allowed, photo_provided):
        self._deny("finish")

    def payment_succeeded(self, ride, now):
        self._deny("payment_succeeded")

    def payment_failed(self, ride, now):
        self._deny("payment_failed")

    def on_long_trip_timeout(self, ride, now):
        self._deny("on_long_trip_timeout")


class ReservedState(RideState):
    name = "reserved"

    def allowed_events(self):
        return ["start", "on_reservation_timeout"]

    def start(self, ride, now, debt_clear, payment_authorized, lock_unlocked, tariff_started):
        if not debt_clear:
            ride._rejection_reason = "У клиента есть задолженность"
            return RejectedState()
        if not payment_authorized:
            ride._rejection_reason = "Не выполнена предварительная авторизация 300 рублей"
            return RejectedState()
        if not lock_unlocked or not tariff_started:
            ride._rejection_reason = "Не выполнена разблокировка или запуск тарификации"
            return RejectedState()
        ride._started_at = now
        return ActiveState()

    def on_reservation_timeout(self, ride, now):
        if now < ride._reserved_at + timedelta(minutes=10):
            raise DomainInvariantViolation("Время бронирования ещё не истекло")
        return ExpiredState()


class ActiveState(RideState):
    name = "active"

    def allowed_events(self):
        return ["pause", "finish", "on_long_trip_timeout"]

    def pause(self, ride, now):
        ride._pause_started_at = now
        return PausedState()

    def finish(self, ride, now, parking_allowed, photo_provided):
        _check_finish_time(ride, now)
        _check_finish_conditions(parking_allowed, photo_provided)
        return AwaitingPaymentState()

    def on_long_trip_timeout(self, ride, now):
        _check_long_trip_time(ride, now)
        return self  # Непрерывающее событие: поездка остаётся активной.


class PausedState(RideState):
    name = "paused"

    def allowed_events(self):
        return ["resume", "finish", "on_long_trip_timeout"]

    def resume(self, ride, now):
        ride._close_pause(now)
        return ActiveState()

    def finish(self, ride, now, parking_allowed, photo_provided):
        _check_finish_time(ride, now)
        _check_finish_conditions(parking_allowed, photo_provided)
        ride._close_pause(now)
        return AwaitingPaymentState()

    def on_long_trip_timeout(self, ride, now):
        _check_long_trip_time(ride, now)
        return self


class AwaitingPaymentState(RideState):
    name = "awaiting_payment"

    def allowed_events(self):
        return ["payment_succeeded", "payment_failed"]

    def payment_succeeded(self, ride, now):
        return PaidState()

    def payment_failed(self, ride, now):
        return DebtState()


class PaidState(RideState):
    name = "paid"


class DebtState(RideState):
    name = "debt_blocked"


class ExpiredState(RideState):
    name = "reservation_expired"


class RejectedState(RideState):
    name = "rejected"


STATES = {
    state.name: state for state in [ReservedState, ActiveState, PausedState,
        AwaitingPaymentState, PaidState, DebtState, ExpiredState, RejectedState]
}


def _check_finish_time(ride, now):
    if ride._started_at is None or now < ride._started_at:
        raise DomainInvariantViolation("Окончание не может быть раньше начала поездки")


def _check_finish_conditions(parking_allowed, photo_provided):
    if not parking_allowed:
        raise DomainInvariantViolation("Завершить поездку можно только в разрешённой зоне")
    if not photo_provided:
        raise DomainInvariantViolation("Для завершения поездки необходимо приложить фотографию")


def _check_long_trip_time(ride, now):
    if ride._started_at is None or now < ride._started_at + timedelta(hours=12):
        raise DomainInvariantViolation("12 часов поездки ещё не истекли")


# Корень агрегата Ride
class Ride:
    RESERVATION_MINUTES = 10
    PREAUTHORIZATION_AMOUNT = 300.0
    PAUSE_RATE_FACTOR = 0.5

    def __init__(self, ride_id: RideId, user_id: UserId, scooter_id: ScooterId,
                 rate: MinuteRate, minimum_cost: Money, reserved_at: datetime,
                 state=None):
        self._id = ride_id
        self._user_id = user_id
        self._scooter_id = scooter_id  # Связь с агрегатом Scooter только по ID
        self._rate = rate
        self._minimum_cost = minimum_cost
        self._reserved_at = reserved_at
        self._started_at = None
        self._ended_at = None
        self._cost = None
        self._pause_started_at = None
        self._paused_seconds = 0
        self._operator_notified = False
        self._rejection_reason = ""
        self._state = state or ReservedState()
        self._journal = []
        self._record("RideReserved", reserved_at, "Бронирование создано")

    @property
    def id(self):
        return self._id

    @property
    def user_id(self):
        return self._user_id

    @property
    def scooter_id(self):
        return self._scooter_id

    @property
    def state(self):
        return self._state.name

    @property
    def allowed_events(self):
        return self._state.allowed_events()

    @property
    def reserved_at(self):
        return self._reserved_at

    @property
    def started_at(self):
        return self._started_at

    @property
    def cost(self):
        return self._cost

    @property
    def rate(self):
        return self._rate

    @property
    def journal(self):
        return list(self._journal)

    @property
    def operator_notified(self):
        return self._operator_notified

    def _record(self, name, now, details=""):
        self._journal.append(DomainEvent(name, now, details))

    def record_event(self, name, now, details=""):
        """Добавляет неизменяемое доменное событие в журнал агрегата."""
        self._record(name, now, details)

    def start(self, now, debt_clear, payment_authorized, lock_unlocked, tariff_started):
        self._state = self._state.start(
            self, now, debt_clear, payment_authorized, lock_unlocked, tariff_started
        )
        if self.state == "active":
            self._record("RideStarted", now, "Проверки пройдены, замок открыт, тарификация запущена")
        else:
            self._record("RideRejected", now, self._rejection_reason)
        return self.state

    def on_reservation_timeout(self, now: datetime):
        self._state = self._state.on_reservation_timeout(self, now)
        self._record("ReservationExpired", now, "Бронь снята через 10 минут")

    def pause(self, now):
        self._state = self._state.pause(self, now)
        self._record("RidePaused", now, "Включена пониженная ставка тарификации")

    def resume(self, now):
        self._state = self._state.resume(self, now)
        self._record("RideResumed", now, "Поездка продолжена")

    def _close_pause(self, now):
        if self._pause_started_at is not None:
            seconds = (now - self._pause_started_at).total_seconds()
            if seconds < 0:
                raise DomainInvariantViolation("Время возобновления раньше начала паузы")
            self._paused_seconds += seconds
            self._pause_started_at = None

    def _calculate_cost(self, now):
        total_seconds = (now - self._started_at).total_seconds()
        if total_seconds < 0:
            raise DomainInvariantViolation("Окончание раньше начала поездки")
        paused_seconds = self._paused_seconds
        if self._pause_started_at is not None:
            paused_seconds += (now - self._pause_started_at).total_seconds()
        total_minutes = ceil(total_seconds / 60)
        paused_minutes = ceil(paused_seconds / 60)
        active_minutes = max(0, total_minutes - paused_minutes)
        amount = active_minutes * self._rate.amount
        amount += paused_minutes * self._rate.amount * self.PAUSE_RATE_FACTOR
        return Money(round(max(amount, self._minimum_cost.amount), 2))

    def finish(self, now, parking_allowed, photo_provided):
        self._state = self._state.finish(self, now, parking_allowed, photo_provided)
        self._ended_at = now
        self._cost = self._calculate_cost(now)
        self._record("RideFinished", now, f"К оплате: {self._cost.amount:.2f} руб.")
        return self._cost

    def payment_succeeded(self, now):
        self._state = self._state.payment_succeeded(self, now)
        self._record("PaymentSucceeded", now, "Стоимость поездки оплачена")

    def payment_failed(self, now):
        self._state = self._state.payment_failed(self, now)
        self._record("DebtCreated", now, "Оплата не прошла, аккаунт заблокирован")

    def on_long_trip_timeout(self, now: datetime):
        if self._operator_notified:
            return False
        self._state = self._state.on_long_trip_timeout(self, now)
        self._operator_notified = True
        self._record("OperatorNotified", now, "Поездка длится более 12 часов")
        return True

    def to_state(self):
        return {
            "id": self._id.value, "user_id": self._user_id.value,
            "scooter_id": self._scooter_id.value, "rate": self._rate.amount,
            "minimum_cost": self._minimum_cost.amount,
            "reserved_at": self._reserved_at.isoformat(), "started_at": self._started_at.isoformat() if self._started_at else None,
            "ended_at": self._ended_at.isoformat() if self._ended_at else None,
            "cost": self._cost.amount if self._cost is not None else None,
            "state": self.state, "pause_started_at": self._pause_started_at.isoformat() if self._pause_started_at else None,
            "paused_seconds": self._paused_seconds, "operator_notified": self._operator_notified,
            "rejection_reason": self._rejection_reason,
            "journal": [{"name": e.name, "occurred_at": e.occurred_at.isoformat(), "details": e.details} for e in self._journal],
        }

    @classmethod
    def restore(cls, data):
        state_name = data["state"]
        if state_name not in STATES:
            raise DomainInvariantViolation("Неизвестное сохранённое состояние поездки")
        ride = cls(
            RideId(data["id"]), UserId(data["user_id"]), ScooterId(data["scooter_id"]),
            MinuteRate(data["rate"]), Money(data["minimum_cost"]),
            datetime.fromisoformat(data["reserved_at"]), STATES[state_name]()
        )
        ride._started_at = datetime.fromisoformat(data["started_at"]) if data["started_at"] else None
        ride._ended_at = datetime.fromisoformat(data["ended_at"]) if data["ended_at"] else None
        ride._cost = Money(data["cost"]) if data["cost"] is not None else None
        ride._pause_started_at = datetime.fromisoformat(data["pause_started_at"]) if data["pause_started_at"] else None
        ride._paused_seconds = data["paused_seconds"]
        ride._operator_notified = data["operator_notified"]
        ride._rejection_reason = data["rejection_reason"]
        if state_name in ("active", "paused") and ride._started_at is None:
            raise DomainInvariantViolation("Для активной поездки отсутствует время начала")
        if state_name in ("awaiting_payment", "paid", "debt_blocked") and (ride._ended_at is None or ride._cost is None):
            raise DomainInvariantViolation("Для завершённой поездки отсутствует время или стоимость")
        ride._journal = [DomainEvent(e["name"], datetime.fromisoformat(e["occurred_at"]), e["details"]) for e in data["journal"]]
        return ride


# Порты репозиториев
class ScooterRepository(Protocol):
    def get(self, scooter_id: ScooterId) -> Scooter: ...
    def save(self, scooter: Scooter) -> None: ...
    def all(self) -> list[Scooter]: ...


class RideRepository(Protocol):
    def get(self, ride_id: RideId) -> Ride: ...
    def save(self, ride: Ride) -> None: ...
    def all(self) -> list[Ride]: ...


# Порты внешних участников
class PaymentGateway(Protocol):
    def preauthorize(self, user_id: UserId, amount: Money) -> bool: ...
    def charge(self, user_id: UserId, amount: Money) -> bool: ...


class ScooterLock(Protocol):
    def unlock(self, scooter_id: ScooterId) -> bool: ...
    def lock(self, scooter_id: ScooterId) -> None: ...


class TariffController(Protocol):
    def start(self, scooter_id: ScooterId, rate: MinuteRate) -> bool: ...
    def pause(self, scooter_id: ScooterId) -> None: ...
    def resume(self, scooter_id: ScooterId) -> None: ...
    def stop(self, scooter_id: ScooterId) -> None: ...


class OperatorNotifier(Protocol):
    def notify_long_trip(self, ride_id: RideId, user_id: UserId) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...
