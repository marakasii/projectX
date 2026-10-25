"""Package 6 connected to the canonical, race-checked case snapshots."""
from datetime import datetime, timezone
from app.common.errors import AppError
from app.common.models import CheckResult, InputSignature
from app.checks.service import FINDINGS


class IntegratedChecksService:
    def __init__(self, cases, checker):
        self.cases, self.checker = cases, checker

    def run(self, owner, case_id):
        snapshot = self.cases.snapshot(owner, case_id)
        result = self.checker.check_case(snapshot)
        self.cases.record_check(owner, result)
        self.cases.rebuild_case(owner, case_id, 'checks_updated')
        return result

    def latest(self, owner, case_id):
        state = self.cases._prepare(owner, case_id)
        row = state['check']
        if row is None:
            raise AppError(404, 'checks_not_found', 'Проверки дела ещё не выполнялись.')
        signature = InputSignature.model_validate_json(row['input_signature_json'])
        result = CheckResult(id=row['id'], case_id=case_id, evaluated_at=row['evaluated_at'],
                            input_signature=signature, findings=FINDINGS.validate_json(row['findings_json']),
                            stale=bool(row['stale']) or signature != state['signature'])
        result.stale |= result.evaluated_at.date() != datetime.now(timezone.utc).date()
        return result
