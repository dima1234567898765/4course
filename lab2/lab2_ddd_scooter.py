from dataclasses import dataclass
from datetime import datetime
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


# Объекты-значения
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
            raise InvalidValueObject("Заряд должен быть от 0 до 100")


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


# Агрегат 1: самокат
class Scooter:
    MIN_BATTERY = 20

    def __init__(self, scooter_id: ScooterId, battery: BatteryLevel,
                 status: str = "free"):
        if status not in ("free", "rented", "maintenance"):
            raise DomainInvariantViolation("Неизвестное состояние самоката")
        if status == "rented" and battery.percent < self.MIN_BATTERY:
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

    def start_rental(self):
        if self._status != "free":
            raise DomainInvariantViolation("Самокат не свободен")
        if self._battery.percent < self.MIN_BATTERY:
            raise DomainInvariantViolation("Заряд ниже 20%")
        self._status = "rented"

    def finish_rental(self, battery: BatteryLevel):
        if self._status != "rented":
            raise DomainInvariantViolation("Самокат не арендован")
        self._battery = battery
        self._status = "free"

    def send_to_maintenance(self):
        if self._status == "rented":
            raise DomainInvariantViolation("Нельзя обслуживать арендованный самокат")
        self._status = "maintenance"

    def to_state(self):
        return {"id": self._id.value, "battery": self._battery.percent,
                "status": self._status}

    @classmethod
    def restore(cls, state):
        # Конструктор повторно проверяет инварианты.
        return cls(ScooterId(state["id"]), BatteryLevel(state["battery"]), state["status"])


# Агрегат 2: поездка
class Ride:
    def __init__(self, ride_id: RideId, user_id: UserId, scooter_id: ScooterId,
                 rate: MinuteRate, minimum_cost: Money, started_at: datetime,
                 ended_at=None, cost=None):
        if ended_at is not None and ended_at < started_at:
            raise DomainInvariantViolation("Окончание раньше начала")
        if (ended_at is None) != (cost is None):
            raise DomainInvariantViolation("Время окончания и стоимость должны быть согласованы")
        self._id = ride_id
        self._user_id = user_id
        self._scooter_id = scooter_id  # Связь с самокатом только по ID
        self._rate = rate
        self._minimum_cost = minimum_cost
        self._started_at = started_at
        self._ended_at = ended_at
        self._cost = cost

    @property
    def id(self):
        return self._id

    @property
    def scooter_id(self):
        return self._scooter_id

    def finish(self, ended_at: datetime) -> Money:
        if self._ended_at is not None:
            raise DomainInvariantViolation("Поездка уже завершена")
        if ended_at < self._started_at:
            raise DomainInvariantViolation("Окончание раньше начала")
        minutes = max(1, ceil((ended_at - self._started_at).total_seconds() / 60))
        amount = max(minutes * self._rate.amount, self._minimum_cost.amount)
        self._ended_at = ended_at
        self._cost = Money(round(amount, 2))
        return self._cost

    def to_state(self):
        return {
            "id": self._id.value,
            "user_id": self._user_id.value,
            "scooter_id": self._scooter_id.value,
            "rate": self._rate.amount,
            "minimum_cost": self._minimum_cost.amount,
            "started_at": self._started_at.isoformat(),
            "ended_at": self._ended_at.isoformat() if self._ended_at else None,
            "cost": self._cost.amount if self._cost is not None else None,
        }

    @classmethod
    def restore(cls, state):
        ended = datetime.fromisoformat(state["ended_at"]) if state["ended_at"] else None
        cost = Money(state["cost"]) if state["cost"] is not None else None
        return cls(RideId(state["id"]), UserId(state["user_id"]),
                   ScooterId(state["scooter_id"]), MinuteRate(state["rate"]),
                   Money(state["minimum_cost"]), datetime.fromisoformat(state["started_at"]),
                   ended, cost)


# Порты репозиториев (Protocol)
class ScooterRepository(Protocol):
    def get(self, scooter_id: ScooterId) -> Scooter: ...
    def save(self, scooter: Scooter) -> None: ...


class RideRepository(Protocol):
    def get(self, ride_id: RideId) -> Ride: ...
    def save(self, ride: Ride) -> None: ...


# Репозитории хранят объекты в памяти
class InMemoryScooterRepository:
    def __init__(self):
        self.items = {}

    def get(self, scooter_id):
        if scooter_id.value not in self.items:
            raise EntityNotFound("Самокат не найден")
        return self.items[scooter_id.value]

    def save(self, scooter):
        self.items[scooter.id.value] = scooter


class InMemoryRideRepository:
    def __init__(self):
        self.items = {}

    def get(self, ride_id):
        if ride_id.value not in self.items:
            raise EntityNotFound("Поездка не найдена")
        return self.items[ride_id.value]

    def save(self, ride):
        self.items[ride.id.value] = ride


# Доменный сервис работает с двумя агрегатами
class ScooterRentalService:
    def __init__(self, scooters: ScooterRepository, rides: RideRepository):
        self.scooters = scooters
        self.rides = rides

    def start_ride(self, ride_id, user_id, scooter_id, rate, minimum_cost, started_at):
        scooter = self.scooters.get(scooter_id)
        ride = Ride(ride_id, user_id, scooter_id, rate, minimum_cost, started_at)
        scooter.start_rental()
        self.scooters.save(scooter)
        self.rides.save(ride)
        return ride

    def finish_ride(self, ride_id, ended_at, battery_after):
        ride = self.rides.get(ride_id)
        scooter = self.scooters.get(ride.scooter_id)
        cost = ride.finish(ended_at)
        scooter.finish_rental(battery_after)
        self.rides.save(ride)
        self.scooters.save(scooter)
        return cost


def main():
    scooters = InMemoryScooterRepository()
    rides = InMemoryRideRepository()
    service = ScooterRentalService(scooters, rides)

    scooter = Scooter(ScooterId("S-001"), BatteryLevel(85))
    scooters.save(scooter)

    ride = service.start_ride(
        RideId("R-001"), UserId("U-001"), ScooterId("S-001"),
        MinuteRate(5.0), Money(30.0), datetime(2026, 10, 11, 10, 0)
    )
    cost = service.finish_ride(
        ride.id, datetime(2026, 10, 11, 10, 17), BatteryLevel(70)
    )

    print("Поездка:", ride.id.value)
    print("Статус самоката:", scooters.get(ScooterId("S-001")).status)
    print("Стоимость поездки:", cost.amount)


if __name__ == "__main__":
    main()
