"""Rebuild from a consistent snapshot; publish only if all inputs still match."""

import json
import sqlite3
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import TypeAdapter

from app.auth.ownership import ResourceKind, require_owned, require_owned_case_document
from app.cases.projection import project_checklist, project_readiness
from app.common.errors import AppError
from app.common.models import (
    Case, CaseCreate, CasePatch, CaseSnapshot, CheckResult, Document, Field, Finding,
    InputSignature, Process, Readiness, TripInput,
)
from app.eligibility.answers import answers_for_current_schema, validate_answers
from app.eligibility.service import technical_finding
from app.events.projection import EventProjector
from app.events.service import event_from_row
from app.profile.service import read_profile
from app.rules.registry import source_quality

ALGORITHM_VERSION = "cases-v1"
FIELDS = TypeAdapter(list[Field])
FINDINGS = TypeAdapter(list[Finding])


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def invalidate_inputs(connection, owner, case_id, reason):
    connection.execute(
        "UPDATE cases SET revision = revision + 1, stale = 1, stale_reason = ?, projection_json = NULL, updated_at = ? "
        "WHERE id = ? AND owner_id = ?", (reason, timestamp(), str(case_id), str(owner.user_id)),
    )
    for table in ("check_results", "routes", "letters"):
        connection.execute(f"UPDATE {table} SET stale = 1 WHERE case_id = ?", (str(case_id),))


def document_from_row(row):
    names = set(Document.model_fields) - {"fields", "quality_findings"}
    return Document(**{name: row[name] for name in names}, fields=FIELDS.validate_json(row["fields_json"]),
                    quality_findings=FINDINGS.validate_json(row["quality_findings_json"]))


class CaseService:
    def __init__(self, database, registry, evaluator, *, document_service=None, checker=None, projector=None):
        self.database, self.registry, self.evaluator = database, registry, evaluator
        self.document_service, self.checker = document_service, checker
        self.projector = projector or EventProjector()

    def _validate_trip(self, scenario, trip, answers):
        if trip.destination_country and trip.destination_country != scenario.destination_country:
            raise AppError(422, "scenario_trip_mismatch", "Страна поездки не совпадает со сценарием.")
        validate_answers(answers, {question.key: question for question in scenario.questions})
        return trip.model_copy(update={"destination_country": scenario.destination_country})

    def create(self, owner, body: CaseCreate) -> Case:
        scenario = self.registry.get_scenario(body.scenario_id)
        trip = self._validate_trip(scenario, body.trip, body.answers)
        identifier, now = uuid4(), timestamp()
        with self.database.transaction() as connection:
            profile = read_profile(connection.execute("SELECT * FROM profiles WHERE owner_id = ?", (str(owner.user_id),)).fetchone())
            # Semantic validation (e.g. overlapping historical stays) precedes persistence.
            self.evaluator.evaluate_applicability(profile, trip, body.answers, scenario)
            connection.execute(
                "INSERT INTO cases (id, owner_id, scenario_id, profile_revision, ruleset_version, trip_json, "
                "answers_json, stale_reason, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'case_created', ?, ?)",
                (str(identifier), str(owner.user_id), scenario.id, profile.revision, scenario.version,
                 trip.model_dump_json(), json.dumps(body.answers), now, now),
            )
        return self.rebuild_case(owner, identifier, "case_created")

    def list(self, owner) -> list[Case]:
        with self.database.connection() as connection:
            identifiers = [UUID(row[0]) for row in connection.execute(
                "SELECT id FROM cases WHERE owner_id = ? ORDER BY created_at DESC, id", (str(owner.user_id),),
            )]
        return [self.get(owner, identifier) for identifier in identifiers]

    def get(self, owner, case_id) -> Case:
        # Source expiry and active rules are evaluated on every read, including restarts.
        return self.rebuild_case(owner, case_id, "case_read")

    def update(self, owner, case_id, patch: CasePatch) -> Case:
        if not patch.model_fields_set:
            return self.get(owner, case_id)
        with self.database.transaction() as connection:
            row = require_owned(connection, owner, ResourceKind.CASE, case_id)
            scenario = self.registry.get_scenario(row["scenario_id"])
            previous_trip = TripInput.model_validate_json(row["trip_json"])
            previous_answers = json.loads(row["answers_json"])
            if ("trip" in patch.model_fields_set and patch.trip is None) or ("answers" in patch.model_fields_set and patch.answers is None):
                raise AppError(422, "invalid_case_patch", "Для изменения используйте объект trip/answers; неизвестное поле задайте null внутри объекта.")
            try:
                trip = TripInput.model_validate(previous_trip.model_dump() | (patch.trip.model_dump(exclude_unset=True) if patch.trip else {}))
            except ValueError as exc:
                raise AppError(422, "invalid_trip", "Проверьте порядок дат поездки.") from exc
            compatible, _ = answers_for_current_schema(previous_answers, {q.key: q for q in scenario.questions})
            answers = compatible | (patch.answers or {})
            trip = self._validate_trip(scenario, trip, answers)
            profile = read_profile(connection.execute("SELECT * FROM profiles WHERE owner_id = ?", (str(owner.user_id),)).fetchone())
            self.evaluator.evaluate_applicability(profile, trip, answers, scenario)
            if trip != previous_trip or answers != previous_answers:
                connection.execute("UPDATE cases SET trip_json = ?, answers_json = ? WHERE id = ? AND owner_id = ?",
                                   (trip.model_dump_json(), json.dumps(answers), str(case_id), str(owner.user_id)))
                invalidate_inputs(connection, owner, case_id, "case_changed")
        return self.rebuild_case(owner, case_id, "case_changed")

    def delete(self, owner, case_id):
        with self.database.transaction() as connection:
            require_owned(connection, owner, ResourceKind.CASE, case_id)
            connection.execute("DELETE FROM cases WHERE id = ? AND owner_id = ?", (str(case_id), str(owner.user_id)))

    def attach(self, owner, case_id, document_id) -> Case:
        with self.database.transaction() as connection:
            require_owned_case_document(connection, owner, case_id, document_id)
            cursor = connection.execute("INSERT OR IGNORE INTO case_documents VALUES (?, ?, ?, ?)",
                                        (str(case_id), str(document_id), str(owner.user_id), timestamp()))
            if cursor.rowcount:
                invalidate_inputs(connection, owner, case_id, "document_attached")
        return self.rebuild_case(owner, case_id, "document_attached")

    def detach(self, owner, case_id, document_id):
        with self.database.transaction() as connection:
            require_owned_case_document(connection, owner, case_id, document_id)
            cursor = connection.execute("DELETE FROM case_documents WHERE case_id = ? AND document_id = ? AND owner_id = ?",
                                        (str(case_id), str(document_id), str(owner.user_id)))
            if cursor.rowcount:
                invalidate_inputs(connection, owner, case_id, "document_detached")
        self.rebuild_case(owner, case_id, "document_detached")

    def _load(self, connection, owner, case_id):
        row = dict(require_owned(connection, owner, ResourceKind.CASE, case_id))
        profile = dict(connection.execute("SELECT * FROM profiles WHERE owner_id = ?", (str(owner.user_id),)).fetchone())
        documents = [dict(item) for item in connection.execute(
            "SELECT d.* FROM documents d JOIN case_documents cd ON cd.document_id = d.id "
            "WHERE cd.case_id = ? AND cd.owner_id = ? AND d.owner_id = ? ORDER BY d.id",
            (str(case_id), str(owner.user_id), str(owner.user_id)),
        )]
        actions = [dict(item) for item in connection.execute("SELECT * FROM checklist_actions WHERE case_id = ? ORDER BY item_id", (str(case_id),))]
        events = [dict(item) for item in connection.execute("SELECT * FROM events WHERE case_id = ? ORDER BY id", (str(case_id),))]
        check = connection.execute("SELECT * FROM check_results WHERE case_id = ? ORDER BY stale ASC, evaluated_at DESC, id DESC LIMIT 1", (str(case_id),)).fetchone()
        route = connection.execute("SELECT revision FROM routes WHERE case_id = ?", (str(case_id),)).fetchone()
        state = {"row": row, "profile": profile, "documents": documents, "actions": actions,
                 "events": events, "check": dict(check) if check else None, "route_revision": route[0] if route else None}
        # Projection/cache writes are excluded, all semantic inputs are included.
        stamp = {**state, "row": {key: row[key] for key in ("id", "owner_id", "scenario_id", "revision", "trip_json", "answers_json")}}
        state["stamp"] = json.dumps(stamp, sort_keys=True)
        return state

    def _prepare(self, owner, case_id):
        with self.database.connection() as connection:
            connection.execute("BEGIN")
            state = self._load(connection, owner, case_id)
        row = state["row"]
        profile = read_profile(state["profile"])
        scenario = self.registry.get_scenario(row["scenario_id"])
        trip = TripInput.model_validate_json(row["trip_json"])
        answers, changed_answers = answers_for_current_schema(json.loads(row["answers_json"]), {q.key: q for q in scenario.questions})
        documents = [document_from_row(item) for item in state["documents"]]
        try:
            facts = self.document_service.get_confirmed_facts(owner.user_id, [doc.id for doc in documents]) if self.document_service else []
        except AppError as exc:
            if exc.status_code == 404:
                raise AppError(409, "input_changed", "Состав документов изменился. Повторите запрос.") from exc
            raise
        revisions = {doc.id: doc.facts_revision for doc in documents}
        if self.document_service and {fact.document_id: fact.facts_revision for fact in facts} != revisions:
            raise AppError(409, "input_changed", "Документы изменились во время чтения. Повторите запрос.")
        signature = InputSignature(
            case_revision=row["revision"], profile_revision=profile.revision, facts_revisions=revisions,
            ruleset_version=scenario.version, algorithm_version=getattr(self.checker, 'algorithm_version', ALGORITHM_VERSION), route_revision=state["route_revision"],
        )
        applicability = self.evaluator.evaluate_applicability(profile, trip, answers, scenario)
        if changed_answers:
            applicability.unknown_conditions.append(technical_finding(
                "answers_schema_changed", "Схема вопросов изменилась: проверьте и сохраните анкету по текущим правилам.", fields=changed_answers,
            ))
            if applicability.status == "applicable":
                applicability.status = "needs_verification"
        events = [event_from_row(event) for event in state["events"]]
        context = {"profile": profile.model_dump(), "trip": trip.model_dump(),
                   "answers": validate_answers(answers, {q.key: q for q in scenario.questions})}
        process = self.projector.project_process(
            events, scenario.process_stages, context=context,
            unverified_source_ids=[source.id for source in scenario.sources if source_quality(source) != "source_checked"
                                   or (applicability.missing_answers and not applicability.requirements)],
        )
        if applicability.status == "unsupported_profile":
            process = Process()
        check = None
        if state["check"]:
            saved = state["check"]
            candidate = CheckResult(id=saved["id"], case_id=case_id, evaluated_at=saved["evaluated_at"],
                                    input_signature=InputSignature.model_validate_json(saved["input_signature_json"]),
                                    findings=FINDINGS.validate_json(saved["findings_json"]), stale=bool(saved["stale"]))
            if not candidate.stale and candidate.input_signature == signature and candidate.evaluated_at.date() == datetime.now(timezone.utc).date():
                check = candidate
        case = Case(
            id=case_id, scenario_id=scenario.id, trip=trip, answers=answers,
            revision=row["revision"], profile_revision=profile.revision, ruleset_version=scenario.version,
            attached_document_ids=list(revisions), checklist=[], readiness=Readiness(), process=process,
            applicability=applicability, stale=False, created_at=row["created_at"], updated_at=row["updated_at"],
        )
        state.update(profile_model=profile, scenario=scenario, document_models=documents, facts=facts,
                     signature=signature, applicability=applicability, process=process, case=case, current_check=check)
        return state

    def _project(self, state):
        case = state["case"]
        check = state["current_check"]
        checklist = project_checklist(
            state["applicability"], state["document_models"], state["facts"], state["signature"],
            {action["item_id"]: action for action in state["actions"]}, check, state["process"],
        )
        findings = [*state["applicability"].barriers, *(check.findings if check else []),
                    *(finding for doc in state["document_models"] for finding in doc.quality_findings)]
        readiness = project_readiness(checklist, applicability=state["applicability"],
                                      coverage_complete=state["scenario"].coverage == "complete", findings=findings)
        return case.model_copy(update={"checklist": checklist, "readiness": readiness})

    def _snapshot(self, owner, state):
        return CaseSnapshot(owner=owner, case=self._project(state), profile=state["profile_model"], scenario=state["scenario"],
                            ruleset=self.registry.get_ruleset(state["scenario"].version), documents=state["facts"], input_signature=state["signature"])

    def snapshot(self, owner, case_id) -> CaseSnapshot:
        """Port for 6/8: values are confirmed at current facts revisions only."""
        return self._snapshot(owner, self._prepare(owner, case_id))

    def _assert_unchanged(self, connection, owner, case_id, state):
        if self._load(connection, owner, case_id)["stamp"] != state["stamp"] or self.registry.active_version != state["signature"].ruleset_version:
            raise AppError(409, "input_changed", "Входные данные изменились во время пересчёта. Повторите запрос.")

    def _validate_check(self, result, state):
        if result.stale or result.case_id != state["case"].id or result.input_signature != state["signature"]:
            raise AppError(409, "stale_check_result", "Результат проверки относится к другим входным данным.")
        ids = set(state["signature"].facts_revisions)
        rule_ids = {rule.id for rule in self.registry.get_ruleset(state["scenario"].version).rules if rule.scenario_id == state["scenario"].id}
        source_ids = {source.id for source in state["scenario"].sources}
        for finding in result.findings:
            if (not set(finding.document_ids) <= ids or not set(finding.source_refs) <= source_ids
                    or (finding.origin == "rule" and (not finding.rule_ref or not finding.source_refs)) or (finding.rule_ref and
                    (finding.rule_ref.version != state["signature"].ruleset_version or finding.rule_ref.id not in rule_ids))):
                raise AppError(422, "invalid_check_result", "Замечание не соответствует снимку дела.")

    def _insert_check(self, connection, result):
        connection.execute("UPDATE check_results SET stale = 1 WHERE case_id = ?", (str(result.case_id),))
        try:
            connection.execute("INSERT INTO check_results VALUES (?, ?, ?, ?, ?, 0)",
                               (str(result.id), str(result.case_id), result.evaluated_at.isoformat(),
                                result.input_signature.model_dump_json(), FINDINGS.dump_json(result.findings).decode()))
        except sqlite3.IntegrityError as exc:
            raise AppError(409, "check_result_conflict", "Идентификатор проверки уже использован.") from exc

    def record_check(self, owner, result: CheckResult):
        """Port for package 6; foreign IDs and late results cannot be published."""
        state = self._prepare(owner, result.case_id)
        self._validate_check(result, state)
        with self.database.transaction() as connection:
            self._assert_unchanged(connection, owner, result.case_id, state)
            self._insert_check(connection, result)
            connection.execute("UPDATE cases SET stale = 1, stale_reason = 'checks_changed', projection_json = NULL WHERE id = ?", (str(result.case_id),))

    def rebuild_case(self, owner, case_id, reason: str) -> Case:
        state = self._prepare(owner, case_id)
        generated = None
        if self.checker is not None and state["current_check"] is None:
            generated = self.checker.check_case(self._snapshot(owner, state))
            self._validate_check(generated, state)
            state["current_check"] = generated
        case = self._project(state)
        with self.database.transaction() as connection:
            self._assert_unchanged(connection, owner, case_id, state)
            if generated is not None:
                self._insert_check(connection, generated)
            # Signature mismatches remain explicit in other stored derivatives.
            if state["row"]["profile_revision"] != case.profile_revision or state["row"]["ruleset_version"] != case.ruleset_version:
                current_id = str(state["current_check"].id) if state["current_check"] else ""
                connection.execute("UPDATE check_results SET stale = 1 WHERE case_id = ? AND id != ?", (str(case_id), current_id))
                for table in ("routes", "letters"):
                    connection.execute(f"UPDATE {table} SET stale = 1 WHERE case_id = ?", (str(case_id),))
            connection.execute(
                "UPDATE cases SET profile_revision = ?, ruleset_version = ?, projection_json = ?, stale = 0, stale_reason = NULL "
                "WHERE id = ? AND owner_id = ?",
                (case.profile_revision, case.ruleset_version, case.model_dump_json(), str(case_id), str(owner.user_id)),
            )
        return case

    def confirm_action(self, owner, case_id, item_id, confirmed: bool) -> Case:
        state = self._prepare(owner, case_id)
        item = next((item for item in self._project(state).checklist if item.id == item_id), None)
        if item is None:
            raise AppError(404, "not_found", "Пункт не найден.")
        if item.kind != "preparation_action" or item.applicability != "yes":
            raise AppError(422, "manual_confirmation_not_allowed", "Документ, внешний этап или неизвестное требование нельзя закрыть этой отметкой.")
        if confirmed and (item.state == "issues" or any(f.severity in ("blocking", "fix") and not f.resolved for f in state["applicability"].barriers)):
            raise AppError(409, "blocking_findings", "Сначала исправьте блокирующие замечания.")
        with self.database.transaction() as connection:
            self._assert_unchanged(connection, owner, case_id, state)
            connection.execute(
                "INSERT INTO checklist_actions VALUES (?, ?, ?, ?, ?) ON CONFLICT(case_id, item_id) DO UPDATE SET "
                "confirmed = excluded.confirmed, input_signature_json = excluded.input_signature_json, updated_at = excluded.updated_at",
                (str(case_id), item_id, int(confirmed), state["signature"].model_dump_json(), timestamp()),
            )
            connection.execute("UPDATE cases SET projection_json = NULL, stale = 1, stale_reason = 'checklist_changed' WHERE id = ?", (str(case_id),))
            connection.execute("UPDATE letters SET stale = 1 WHERE case_id = ?", (str(case_id),))
        return self.rebuild_case(owner, case_id, "checklist_changed")
