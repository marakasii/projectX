"""Owner-scoped routes in package 1's existing tables; no schema changes."""

import logging
from datetime import datetime, timezone
from uuid import UUID

from app.auth.ownership import ResourceKind, require_owned
from app.common.errors import AppError
from app.common.models import Finding, InputSignature, Route, RouteResult, TripInput
from app.profile.service import read_profile
from .engine import ALGORITHM_VERSION, TransitChecker, validate_route


class ItineraryService:
    def __init__(self, database, registry, *, checker=None, rebuilder=None, clock=None):
        self.database, self.registry, self.rebuilder = database, registry, rebuilder
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.checker = checker or TransitChecker(self.clock)

    def _read_state(self, connection, owner, case_id):
        case = dict(require_owned(connection, owner, ResourceKind.CASE, case_id))
        profile = read_profile(connection.execute(
            "SELECT * FROM profiles WHERE owner_id = ?", (str(owner.user_id),),
        ).fetchone())
        route = connection.execute("SELECT * FROM routes WHERE case_id = ?", (str(case_id),)).fetchone()
        facts = connection.execute(
            "SELECT d.id, d.facts_revision FROM case_documents cd JOIN documents d ON d.id = cd.document_id "
            "WHERE cd.case_id = ? AND cd.owner_id = ? AND d.owner_id = ? ORDER BY d.id",
            (str(case_id), str(owner.user_id), str(owner.user_id)),
        ).fetchall()
        return dict(case=case, profile=profile, route=dict(route) if route is not None else None,
                    facts={UUID(row["id"]): row["facts_revision"] for row in facts})

    def _signature(self, state):
        return InputSignature(
            case_revision=state["case"]["revision"], profile_revision=state["profile"].revision,
            facts_revisions=state["facts"], ruleset_version=self.registry.transit_version,
            algorithm_version=ALGORITHM_VERSION, route_revision=state["route"]["revision"] if state["route"] else None,
        )

    def _rebuild(self, owner, case_id, reason):
        if self.rebuilder is not None:
            try:
                self.rebuilder.rebuild_case(owner, case_id, reason)
            except Exception as exc:
                logging.getLogger(__name__).warning("Case rebuild deferred: %s", type(exc).__name__)

    def save(self, owner, case_id, route: Route) -> Route:
        try:
            validate_route(route)
        except ValueError as exc:
            raise AppError(422, "invalid_route", str(exc)) from exc
        serialized, changed = route.model_dump_json(), False
        with self.database.transaction() as connection:
            state = self._read_state(connection, owner, case_id)
            previous = state["route"]
            if previous is None or previous["route_json"] != serialized:
                changed = True
                now = self.clock().isoformat()
                connection.execute(
                    "INSERT INTO routes (case_id,route_json,updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(case_id) DO UPDATE SET revision = routes.revision + 1, route_json = excluded.route_json, "
                    "result_json = NULL, input_signature_json = NULL, stale = 1, updated_at = excluded.updated_at",
                    (str(case_id), serialized, now),
                )
                connection.execute(
                    "UPDATE cases SET revision = revision + 1, stale = 1, stale_reason = 'route_updated', "
                    "projection_json = NULL, updated_at = ? WHERE id = ? AND owner_id = ?",
                    (now, str(case_id), str(owner.user_id)),
                )
                for table in ("check_results", "letters"):
                    connection.execute(f"UPDATE {table} SET stale = 1 WHERE case_id = ?", (str(case_id),))
        if changed:
            self._rebuild(owner, case_id, "route_updated")
        return route.model_copy(deep=True)

    def get_route(self, owner, case_id) -> Route:
        with self.database.transaction() as connection:
            state = self._read_state(connection, owner, case_id)
        if state["route"] is None:
            raise AppError(404, "route_not_found", "Маршрут дела ещё не сохранён.")
        return Route.model_validate_json(state["route"]["route_json"])

    def run(self, owner, case_id) -> RouteResult:
        with self.database.transaction() as connection:
            state = self._read_state(connection, owner, case_id)
        if state["route"] is None:
            raise AppError(404, "route_not_found", "Сначала сохраните маршрут дела.")
        if self.registry is None:
            raise AppError(503, "transit_rules_unavailable", "Реестр транзитных правил не подключён.")
        if self.rebuilder is not None and (
            state["case"]["profile_revision"] != state["profile"].revision
            or state["case"]["ruleset_version"] != self.registry.active_version
        ):
            self._rebuild(owner, case_id, "route_inputs_refresh")
            with self.database.transaction() as connection:
                state = self._read_state(connection, owner, case_id)
            if state["route"] is None:
                raise AppError(409, "route_inputs_changed", "Маршрут изменился. Повторите проверку.")
        signature = self._signature(state)
        as_of = self.clock().astimezone(timezone.utc).date()
        ruleset = self.registry.get_ruleset(signature.ruleset_version, as_of=as_of)
        route = Route.model_validate_json(state["route"]["route_json"])
        result = self.checker.check_route(state["profile"], route, ruleset)
        destination = TripInput.model_validate_json(state["case"]["trip_json"]).destination_country
        if destination is not None and not any(destination in (leg.departure_country, leg.arrival_country) for leg in route.legs):
            result = result.model_copy(deep=True)
            result.findings.append(Finding(
                code="route_destination_mismatch", severity="verify", origin="heuristic",
                message="В маршруте не указана страна назначения дела.",
                next_action="Сверьте участки маршрута с выбранной поездкой.",
                field_paths=["case.trip.destination_country", "route.legs"],
            ))
            if result.status != "blocked":
                result.status = "needs_verification"
        with self.database.transaction() as connection:
            current = self._read_state(connection, owner, case_id)
            if (signature != self._signature(current) or current["route"] is None
                or current["route"]["route_json"] != state["route"]["route_json"]
                or as_of != self.clock().astimezone(timezone.utc).date()):
                raise AppError(409, "route_inputs_changed", "Во время проверки изменились маршрут, профиль или дело. Повторите проверку.")
            now = self.clock().isoformat()
            connection.execute(
                "UPDATE routes SET result_json = ?, input_signature_json = ?, stale = 0, updated_at = ? WHERE case_id = ?",
                (result.model_dump_json(), signature.model_dump_json(), now, str(case_id)),
            )
            connection.execute(
                "UPDATE cases SET stale = 1, stale_reason = 'route_checked', projection_json = NULL, updated_at = ? "
                "WHERE id = ? AND owner_id = ?", (now, str(case_id), str(owner.user_id)),
            )
        self._rebuild(owner, case_id, "route_checked")
        return result

    def latest(self, owner, case_id) -> RouteResult:
        """Internal package 4 port; the public contract does not define GET route/check."""
        with self.database.transaction() as connection:
            state = self._read_state(connection, owner, case_id)
        row = state["route"]
        if row is None or row["result_json"] is None:
            raise AppError(404, "route_checks_not_found", "Актуальная проверка маршрута ещё не сохранена.")
        if (self.registry is None or row["stale"] or row["input_signature_json"] is None
            or InputSignature.model_validate_json(row["input_signature_json"]) != self._signature(state)
            or datetime.fromisoformat(row["updated_at"]).astimezone(timezone.utc).date() != self.clock().astimezone(timezone.utc).date()):
            raise AppError(409, "route_checks_stale", "Результат проверки маршрута устарел. Выполните проверку снова.")
        return RouteResult.model_validate_json(row["result_json"])
