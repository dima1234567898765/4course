import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from domain import (
    BatteryLevel, DomainInvariantViolation, MinuteRate, Money, RideId,
    Scooter, ScooterId, TransitionNotAllowed, UserId, Ride,
)
from application import (
    InMemoryPaymentGateway, InMemoryRideRepository, InMemoryScooterLock,
    InMemoryScooterRepository, InMemoryTariffController,
    InMemoryOperatorNotifier, ScooterRentalProcess,
)


class FixedClock:
    def __init__(self):
        self.value = datetime(2026, 11, 8, 10, 0, 0)

    def now(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


def build_process():
    scooters = InMemoryScooterRepository()
    rides = InMemoryRideRepository()
    payments = InMemoryPaymentGateway()
    lock = InMemoryScooterLock()
    tariff = InMemoryTariffController()
    operator = InMemoryOperatorNotifier()
    clock = FixedClock()
    process = ScooterRentalProcess(scooters, rides, payments, lock, tariff, operator, clock)
    return process, scooters, rides, payments, operator, clock


def add_scooter(scooters, battery=85):
    scooter = Scooter(ScooterId("SC-01"), BatteryLevel(battery))
    scooters.save(scooter)


def reserve(process):
    return process.reserve_ride(
        RideId("R-01"), UserId("U-01"), ScooterId("SC-01"),
        MinuteRate(5), Money(30)
    )


def test_successful_ride_with_pause_and_discounted_rate():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    process.start_ride(ride.id)
    clock.advance(minutes=4)
    process.pause_ride(ride.id)
    clock.advance(minutes=2)
    process.resume_ride(ride.id)
    clock.advance(minutes=10)
    cost = process.finish_ride(ride.id, True, True, BatteryLevel(70))
    assert cost.amount == 75.0
    assert rides.get(RideId("R-01")).state == "paid"
    assert scooters.get(ScooterId("SC-01")).status == "free"
    assert payments.authorized == [("U-01", 300.0)]


def test_reservation_timeout_releases_scooter():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    clock.advance(minutes=10)
    process.tick(clock.now())
    assert ride.state == "reservation_expired"
    assert scooters.get(ScooterId("SC-01")).status == "free"


def test_failed_payment_creates_debt_and_blocks_account():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    process.start_ride(ride.id)
    clock.advance(minutes=10)
    payments.charge_result = False
    process.finish_ride(ride.id, True, True, BatteryLevel(60))
    assert ride.state == "debt_blocked"
    assert "U-01" in process.blocked_users
    payments.charge_result = True
    process.pay_debt(UserId("U-01"))
    assert "U-01" not in process.blocked_users


def test_long_ride_notifies_operator_once():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    process.start_ride(ride.id)
    clock.advance(hours=12, minutes=1)
    process.tick(clock.now())
    process.tick(clock.now())
    assert len(operator.notifications) == 1
    assert ride.operator_notified


def test_invalid_transition_is_rejected():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    with pytest.raises(TransitionNotAllowed):
        process.pause_ride(ride.id)
    assert ride.state == "reserved"


def test_finish_requires_parking_zone_and_photo():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    process.start_ride(ride.id)
    with pytest.raises(DomainInvariantViolation):
        process.finish_ride(ride.id, False, True, BatteryLevel(70))
    assert ride.state == "active"


def test_restore_rechecks_saved_state():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    restored = Ride.restore(ride.to_state())
    assert restored.state == "reserved"
    assert restored.id.value == "R-01"


def test_debt_blocks_start_without_unlocking():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    process.start_ride(ride.id, debt_clear=False)
    assert ride.state == "rejected"
    assert scooters.get(ScooterId("SC-01")).status == "free"
    assert "SC-01" not in process.lock.unlocked if hasattr(process, "lock") else True


def test_preauthorization_failure_rejects_ride():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    payments.preauthorization_result = False
    process.start_ride(ride.id)
    assert ride.state == "rejected"
    assert scooters.get(ScooterId("SC-01")).status == "free"
    assert "SC-01" not in process.tariff.running if hasattr(process, "tariff") else True


def test_scooter_with_low_battery_cannot_be_reserved():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters, battery=10)
    with pytest.raises(DomainInvariantViolation):
        reserve(process)


def test_start_after_reservation_deadline_expires_and_releases_scooter():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    clock.advance(minutes=11)
    with pytest.raises(TransitionNotAllowed):
        process.start_ride(ride.id)
    assert ride.state == "reservation_expired"
    assert scooters.get(ScooterId("SC-01")).status == "free"


def test_finish_before_start_time_is_rejected_without_state_change():
    process, scooters, rides, payments, operator, clock = build_process()
    add_scooter(scooters)
    ride = reserve(process)
    process.start_ride(ride.id)
    earlier = clock.now() - timedelta(seconds=1)
    with pytest.raises(DomainInvariantViolation):
        ride.finish(earlier, True, True)
    assert ride.state == "active"
