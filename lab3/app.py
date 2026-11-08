"""Flask-веб-адаптер. Доменный и прикладной слои Flask не импортируют."""
import logging
import os
from datetime import datetime, timedelta

from flask import Flask, jsonify, request

from application import (
    InMemoryPaymentGateway, InMemoryRideRepository,
    InMemoryScooterLock, InMemoryScooterRepository, InMemoryTariffController,
    InMemoryOperatorNotifier, ScooterRentalProcess,
)
from domain import (
    BatteryLevel, ClockMovedBackwards, DomainInvariantViolation,
    EntityNotFound, GatewayUnresolved, InvalidValueObject, Money,
    MinuteRate, RideId, Scooter, ScooterId, TransitionNotAllowed, UserId,
)


class AdjustableClock:
    """Инфраструктурные часы со сдвигом; время назад не переводится."""
    def __init__(self):
        self.offset = timedelta(0)

    def now(self):
        return datetime.now() + self.offset

    def set_time(self, target):
        current = self.now()
        if target < current:
            raise ClockMovedBackwards("Нельзя перевести часы приложения назад")
        self.offset = target - datetime.now()
        return self.now()


def create_app():
    app = Flask(__name__)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())

    scooters = InMemoryScooterRepository()
    rides = InMemoryRideRepository()
    payments = InMemoryPaymentGateway()
    lock = InMemoryScooterLock()
    tariff = InMemoryTariffController()
    operator = InMemoryOperatorNotifier()
    clock = AdjustableClock()
    process = ScooterRentalProcess(scooters, rides, payments, lock, tariff, operator, clock)

    app.extensions["services"] = {
        "scooters": scooters, "rides": rides, "payments": payments,
        "lock": lock, "tariff": tariff, "operator": operator,
        "clock": clock, "process": process,
    }

    def parse_time(value):
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            raise InvalidValueObject("Дата должна быть в формате ISO 8601")

    def parse_number(value, name):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise InvalidValueObject(f"Поле '{name}' должно быть числом")

    def ride_json(ride):
        return {
            "id": ride.id.value,
            "user_id": ride.user_id.value,
            "scooter_id": ride.scooter_id.value,
            "state": ride.state,
            "allowed_events": ride.allowed_events,
            "cost": ride.cost.amount if ride.cost else None,
            "journal": [
                {"event": event.name, "time": event.occurred_at.isoformat(),
                 "details": event.details}
                for event in ride.journal
            ],
        }

    def scooter_json(scooter):
        return {"id": scooter.id.value, "battery": scooter.battery.percent,
                "status": scooter.status}

    @app.errorhandler(TransitionNotAllowed)
    def transition_error(error):
        return jsonify(error="transition_not_allowed", message=str(error)), 409

    @app.errorhandler(DomainInvariantViolation)
    @app.errorhandler(InvalidValueObject)
    def domain_validation_error(error):
        return jsonify(error="domain_validation", message=str(error)), 422

    @app.errorhandler(EntityNotFound)
    def not_found(error):
        return jsonify(error="not_found", message=str(error)), 404

    @app.errorhandler(ClockMovedBackwards)
    def clock_error(error):
        return jsonify(error="clock_moved_backwards", message=str(error)), 422

    @app.errorhandler(GatewayUnresolved)
    def gateway_error(error):
        return jsonify(error="gateway_unresolved", message=str(error)), 500

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.post("/scooters")
    def create_scooter():
        data = request.get_json(silent=True) or {}
        scooter = Scooter(
            ScooterId(data.get("id", "")),
            BatteryLevel(data.get("battery", 0)),
        )
        scooters.save(scooter)
        return jsonify(scooter_json(scooter)), 201

    @app.get("/scooters/<scooter_id>")
    def get_scooter(scooter_id):
        scooter = scooters.get(ScooterId(scooter_id))
        return jsonify(scooter_json(scooter))

    @app.post("/rides")
    def reserve_ride():
        data = request.get_json(silent=True) or {}
        ride = process.reserve_ride(
            RideId(data.get("id", "")),
            UserId(data.get("user_id", "")),
            ScooterId(data.get("scooter_id", "")),
            MinuteRate(parse_number(data.get("rate", 5), "rate")),
            Money(parse_number(data.get("minimum_cost", 30), "minimum_cost")),
        )
        return jsonify(ride_json(ride)), 201

    @app.get("/rides/<ride_id>")
    def get_ride(ride_id):
        ride = rides.get(RideId(ride_id))
        return jsonify(ride_json(ride))

    @app.post("/rides/<ride_id>/start")
    def start_ride(ride_id):
        data = request.get_json(silent=True) or {}
        payments.preauthorization_result = data.get("preauthorization_success", True)
        lock.unlock_result = data.get("unlock_success", True)
        tariff.start_result = data.get("tariff_success", True)
        ride = process.start_ride(RideId(ride_id), data.get("debt_clear", True))
        return jsonify(ride_json(ride))

    @app.post("/rides/<ride_id>/pause")
    def pause_ride(ride_id):
        return jsonify(ride_json(process.pause_ride(RideId(ride_id))))

    @app.post("/rides/<ride_id>/resume")
    def resume_ride(ride_id):
        return jsonify(ride_json(process.resume_ride(RideId(ride_id))))

    @app.post("/rides/<ride_id>/finish")
    def finish_ride(ride_id):
        data = request.get_json(silent=True) or {}
        payments.charge_result = data.get("payment_success", True)
        cost = process.finish_ride(
            RideId(ride_id), data.get("parking_allowed", False),
            data.get("photo_provided", False),
            BatteryLevel(data.get("battery_after", 0)),
        )
        ride = rides.get(RideId(ride_id))
        result = ride_json(ride)
        result["cost"] = cost.amount
        result["account_blocked"] = ride.user_id.value in process.blocked_users
        return jsonify(result)

    @app.post("/users/<user_id>/pay-debt")
    def pay_debt(user_id):
        data = request.get_json(silent=True) or {}
        payments.charge_result = data.get("payment_success", True)
        paid = process.pay_debt(UserId(user_id))
        return jsonify(user_id=user_id, debt_paid=paid.amount, blocked=False)

    @app.post("/internal/tick")
    def tick():
        data = request.get_json(silent=True) or {}
        if data.get("now"):
            now = clock.set_time(parse_time(data["now"]))
        else:
            now = clock.now()
        changed = process.tick(now)
        return jsonify(now=now.isoformat(), processed_rides=changed,
                       operator_notifications=len(operator.notifications))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
