"""Adapt package 6 to package 4's snapshot/record_check ports.

CaseService owns the snapshot envelope (including route_revision). The pure
engine validates document axes and versions independently before its findings
can be returned in that envelope. No fields/rules are supplied by HTTP clients.
"""

from datetime import datetime, timezone

from app.common.errors import AppError
from app.common.models import CheckResult, InputSignature
from .engine import ALGORITHM_VERSION, DocumentChecker, signature
from .service import FINDINGS


class CaseCheckerAdapter:
    algorithm_version = ALGORITHM_VERSION

    def __init__(self, checker=None, *, clock=None):
        self.checker = checker or DocumentChecker(clock)

    def check_case(self, snapshot):
        result = self.checker.check_case(snapshot)
        if result.stale or result.case_id != snapshot.case.id or result.input_signature != signature(snapshot):
            raise AppError(409, "invalid_check_snapshot", "Проверка не соответствует снимку дела. Получите новый снимок.")
        return result.model_copy(update={"input_signature": snapshot.input_signature.model_copy(deep=True)})


class CaseChecksService:
    """HTTP checks using package 4's actual ownership, race checks and rebuild."""

    def __init__(self, cases, checker, *, clock=None):
        self.cases, self.checker = cases, checker
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def run(self, owner, case_id):
        result = self.checker.check_case(self.cases.snapshot(owner, case_id))
        self.cases.record_check(owner, result)
        self.cases.rebuild_case(owner, case_id, "checks_changed")
        return result

    def latest(self, owner, case_id):
        snapshot = self.cases.snapshot(owner, case_id)
        with self.cases.database.connection() as connection:
            row = connection.execute("SELECT * FROM check_results WHERE case_id = ? ORDER BY rowid DESC LIMIT 1",
                                     (str(case_id),)).fetchone()
        if row is None:
            raise AppError(404, "checks_not_found", "Проверки дела ещё не выполнялись.")
        result = CheckResult(
            id=row["id"], case_id=row["case_id"], evaluated_at=row["evaluated_at"],
            input_signature=InputSignature.model_validate_json(row["input_signature_json"]), findings=FINDINGS.validate_json(row["findings_json"]), stale=bool(row["stale"]),
        )
        result.stale = (result.stale or result.input_signature != snapshot.input_signature
                        or result.evaluated_at.astimezone(timezone.utc).date() != self.clock().astimezone(timezone.utc).date())
        return result

