"""Owner-scoped event persistence; only process projections become dirty."""

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.auth.ownership import ResourceKind, require_owned
from app.common.errors import AppError
from app.common.models import Event, EventCreate, Notification, TripInput
from app.eligibility.answers import answers_for_current_schema, validate_answers
from app.eligibility.conditions import evaluate_condition
from app.profile.service import read_profile


def event_from_row(row) -> Event:
    return Event(**{name: row[name] for name in Event.model_fields})


class EventService:
    def __init__(self, database, registry, evaluator):
        self.database, self.registry, self.evaluator = database, registry, evaluator

    def list(self, owner, case_id: UUID) -> list[Event]:
        with self.database.connection() as connection:
            require_owned(connection, owner, ResourceKind.CASE, case_id)
            events = [event_from_row(row) for row in connection.execute(
                "SELECT * FROM events WHERE case_id = ?", (str(case_id),),
            )]
        return sorted(events, key=lambda event: (event.occurred_at, event.received_at, str(event.id)))

    def create(self, owner, case_id: UUID, body: EventCreate) -> Event:
        now = datetime.now(timezone.utc)
        with self.database.transaction() as connection:
            case = require_owned(connection, owner, ResourceKind.CASE, case_id)
            if body.occurred_at > now:
                raise AppError(422, "event_in_future", "Произошедшее событие не может быть в будущем.")
            scenario = self.registry.get_scenario(case["scenario_id"])
            profile = read_profile(connection.execute(
                "SELECT * FROM profiles WHERE owner_id = ?", (str(owner.user_id),),
            ).fetchone())
            trip = TripInput.model_validate_json(case["trip_json"])
            answers, changed = answers_for_current_schema(json.loads(case["answers_json"]), {q.key: q for q in scenario.questions})
            if changed:
                raise AppError(422, "answers_schema_changed", "Проверьте и сохраните анкету по текущей схеме перед новым событием.")
            applicability = self.evaluator.evaluate_applicability(profile, trip, answers, scenario)
            if applicability.status == "unsupported_profile" or (applicability.missing_answers and not applicability.requirements):
                raise AppError(422, "event_needs_input", "Сначала уточните поддерживаемый профиль и поездку.")
            context = {"profile": profile.model_dump(), "trip": trip.model_dump(),
                       "answers": validate_answers(answers, {q.key: q for q in scenario.questions})}
            templates = [stage for stage in scenario.process_stages if body.event_type in stage.event_types]
            if not templates:
                raise AppError(422, "event_not_allowed", "Событие не предусмотрено сценарием этого дела.")
            applicability = [evaluate_condition(stage.applies_if, context).value for stage in templates]
            if "yes" not in applicability:
                code = "event_needs_input" if "unknown" in applicability else "event_not_applicable"
                raise AppError(422, code, "Сначала уточните применимость этапа по анкете.")
            if body.event_type == "decision_received":
                if body.outcome not in ("approved", "refused"):
                    raise AppError(422, "decision_outcome_required", "Для решения явно укажите approved или refused.")
            elif body.outcome is not None:
                raise AppError(422, "unexpected_outcome", "Исход указывается только для события decision_received.")
            event = Event(
                **body.model_dump(), id=uuid4(), case_id=case_id, received_at=now, source="manual",
            )
            connection.execute(
                "INSERT INTO events (id, case_id, event_type, occurred_at, received_at, source, note, outcome) "
                "VALUES (?, ?, ?, ?, ?, 'manual', ?, ?)",
                (str(event.id), str(case_id), event.event_type, event.occurred_at.isoformat(),
                 now.isoformat(), event.note, event.outcome),
            )
            connection.execute(
                "UPDATE cases SET projection_json = NULL, stale = 1, stale_reason = 'events_changed', updated_at = ? "
                "WHERE id = ? AND owner_id = ?", (now.isoformat(), str(case_id), str(owner.user_id)),
            )
            connection.execute("UPDATE letters SET stale = 1 WHERE case_id = ?", (str(case_id),))
        return event


def build_notifications(case, events: list[Event]) -> list[Notification]:
    result = []
    seen = set()
    if case.applicability:
        for finding in [*case.applicability.barriers, *case.applicability.unknown_conditions]:
            if not finding.resolved and finding.message not in seen:
                seen.add(finding.message)
                result.append(Notification(code=finding.code, message=finding.message, next_action=finding.next_action))
        for field in case.applicability.missing_answers:
            result.append(Notification(code="answer_needed", message=f"Не заполнено {field}.", next_action="Дополните анкету профиля или поездки."))
    for item in case.checklist:
        if item.mandatory and item.applicability != "no" and item.state != "satisfied":
            result.append(Notification(code="checklist_action", message=item.title, next_action=item.next_action or item.explanation))
    for stage in case.process.stages:
        if stage.id == case.process.current_stage:
            result.append(Notification(code="process_next_action", message=stage.title, next_action="После фактического выполнения добавьте ручное событие."))
    if case.process.outcome == "refused":
        result.append(Notification(code="decision_refused", message="Вручную отмечено решение об отказе.", next_action="Ознакомьтесь с официальным решением; готовность документов не означает одобрение."))
    for event in events[-10:]:
        result.append(Notification(code="manual_event", message=f"В хронологии: {event.event_type}.", next_action="Сверьте запись с фактическим событием.", event_id=event.id))
    return result
