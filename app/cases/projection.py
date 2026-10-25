"""Readiness is a document/preparation measure, independent of process events."""

from app.common.models import ChecklistItem, Finding, Readiness
from app.documents.fields import FIELD_KEYS


def project_checklist(applicability, documents, confirmed_facts, signature, actions, check, process):
    facts = {item.document_id: {field.key for field in item.fields} for item in confirmed_facts}
    check_findings = [finding for finding in check.findings if not finding.resolved] if check else []
    result = []
    for requirement in applicability.requirements:
        matching = [document for document in documents if document.document_type == requirement.document_type]
        ids = [document.id for document in matching]
        rule_ids = {ref.id for ref in requirement.rule_refs}
        findings = [finding for finding in [*applicability.barriers, *check_findings]
                    if (set(finding.document_ids) & set(ids)) or
                    (finding.rule_ref and finding.rule_ref.id in rule_ids) or
                    (not finding.document_ids and not finding.rule_ref and finding.severity in ("blocking", "fix", "verify"))]
        findings.extend(finding for document in matching for finding in document.quality_findings if not finding.resolved)
        state, method = "missing", None
        explanation = requirement.explanation
        next_action = "Подготовьте требование и подтвердите его допустимым способом."
        if requirement.applicability == "no":
            state, next_action = "not_applicable", None
        elif requirement.applicability == "unknown":
            state, next_action = "needs_verification", "Уточните недостающий ответ или источник требования."
        elif any(finding.severity in ("blocking", "fix") for finding in findings):
            state, next_action = "issues", "Исправьте замечания и повторите проверку."
        elif any(finding.severity == "verify" for finding in findings):
            state, next_action = "needs_verification", "Проверьте перечисленные неопределённости."
        elif requirement.kind == "document":
            if matching:
                state = "received"
                next_action = "Извлеките, сверьте и подтвердите все ключевые поля документа."
                complete = any(set(FIELD_KEYS[document.document_type.value]) <= facts.get(document.id, set()) for document in matching)
                if complete:
                    state, next_action = "needs_verification", "Выполните проверку документов пакетом 6; загрузка и подтверждение полей её не заменяют."
                    if check is not None:
                        state, method, next_action = "satisfied", "rules_checked", None
            else:
                next_action = "Прикрепите свой документ из личного архива."
        elif requirement.kind == "preparation_action":
            action = actions.get(requirement.id)
            if action and action["confirmed"] and action["input_signature_json"] == signature.model_dump_json():
                state, method, next_action = "satisfied", "user_confirmed", None
            else:
                next_action = "После фактического выполнения подтвердите действие для текущих входных данных."
        else:
            stage = next((stage for stage in process.stages if stage.id == requirement.id), None)
            if stage and stage.completed:
                state, method, next_action = "satisfied", "user_confirmed", None
            else:
                state, next_action = "needs_verification", "Отметьте фактическое событие в отдельной хронологии процесса."
        result.append(ChecklistItem(
            id=requirement.id, requirement_id=requirement.id, title=requirement.title,
            kind=requirement.kind, mandatory=requirement.mandatory, applicability=requirement.applicability,
            readiness_contributing=requirement.readiness_contributing, state=state,
            document_ids=ids, verification_method=method, findings=findings,
            explanation=explanation, source_refs=requirement.source_ids, next_action=next_action,
        ))
    return result


def project_readiness(checklist: list[ChecklistItem], *, applicability, coverage_complete: bool, findings: list[Finding]) -> Readiness:
    required = [item for item in checklist if item.mandatory and item.readiness_contributing and item.applicability == "yes"]
    satisfied = sum(item.state == "satisfied" for item in required)
    unknown = [item.message for item in applicability.unknown_conditions if not item.resolved and item.severity != "info"]
    unknown.extend(f"Нужен ответ: {field}" for field in applicability.missing_answers)
    unknown.extend(item.title for item in checklist if item.mandatory and item.applicability == "unknown")
    unknown.extend(item.title for item in required if item.state == "needs_verification")
    unknown.extend(item.message for item in findings if not item.resolved and item.severity == "verify")
    if not coverage_complete:
        unknown.append("Полнота официального списка требований не подтверждена.")
    if applicability.status == "unsupported_profile":
        unknown.append("Профиль не входит в поддерживаемый сценарий.")
    blocking = [item for item in findings if not item.resolved and item.severity in ("blocking", "fix")]
    denominator_known = coverage_complete and not any(item.mandatory and item.applicability == "unknown" for item in checklist)
    percent = 100 * satisfied // len(required) if required and denominator_known else None
    ready = percent == 100 and not unknown and not blocking and applicability.status == "applicable"
    reasons = []
    if unknown or percent is None:
        reasons.append("needs_verification")
    if blocking:
        reasons.append("blocking_findings")
    if satisfied != len(required):
        reasons.append("requirements_unmet")
    return Readiness(
        percent=percent, required_total=len(required), satisfied_required=satisfied, ready=ready,
        unknown_required_conditions=list(dict.fromkeys(unknown)), blocking_count=len(blocking), reasons=reasons,
    )
