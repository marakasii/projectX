"""Pure checks over a frozen CaseSnapshot; no AI, network or persistence."""

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError

from app.common.dates import add_calendar_months
from app.common.errors import AppError
from app.common.models import (
    CaseSnapshot, CheckResult, Destination, Finding, InputSignature, RuleRef,
)
from app.documents.fields import FIELD_KEYS, normalize
from app.eligibility.answers import validate_answers
from app.eligibility.conditions import evaluate_condition, lookup
from app.eligibility.service import EligibilityService
from app.rules.registry import source_quality
from app.rules.schema import (
    FundsParameters, InsuranceParameters, PassportMaxAgeParameters,
    PassportValidityParameters, parse_parameters,
)

ALGORITHM_VERSION = "documents-1"
DATE_KEYS = {"birth_date", "issued_on", "expires_on", "statement_on", "starts_on", "ends_on"}
AMOUNT_KEYS = {"balance", "coverage_amount"}
# Supported comparison currencies, not a claim to implement the entire ISO catalog.
CURRENCIES = {"RUB", "EUR", "USD", "GBP", "JPY", "INR", "EGP", "ZAR", "CAD", "BRL", "ARS", "AUD", "NZD", "TRY", "AED", "CHF", "CNY"}
COUNTRY_LABELS = {
    "FRANCE": "FR", "SPAIN": "ES", "JAPAN": "JP", "INDIA": "IN", "EGYPT": "EG",
    "SOUTH AFRICA": "ZA", "USA": "US", "CANADA": "CA", "BRAZIL": "BR", "ARGENTINA": "AR",
    "AUSTRALIA": "AU", "NEW ZEALAND": "NZ", "RUSSIA": "RU", "TURKEY": "TR", "UAE": "AE",
}


def text(value: str) -> str:
    return " ".join(value.split()).casefold()


def signature(snapshot: CaseSnapshot) -> InputSignature:
    return InputSignature(
        case_revision=snapshot.case.revision, profile_revision=snapshot.profile.revision,
        facts_revisions={doc.document_id: doc.facts_revision for doc in snapshot.documents},
        ruleset_version=snapshot.ruleset.version, algorithm_version=ALGORITHM_VERSION,
        route_revision=snapshot.input_signature.route_revision,
    )


class _SnapshotRegistry:
    """Read-only adapter for reusing package 3 on exactly the supplied snapshot.

    This is not a substitute country registry or fixture: it only exposes the
    real scenario/ruleset already present in the shared CaseSnapshot.
    """

    def __init__(self, snapshot):
        self.scenario, self.ruleset = snapshot.scenario, snapshot.ruleset

    def destinations(self):
        scenario = self.scenario
        return [Destination(
            country=scenario.destination_country, name=scenario.destination_country,
            scenario_id=scenario.id, version=scenario.version,
            entry_mode=scenario.entry_mode, data_quality=scenario.data_quality,
        )]

    def get_scenario(self, identifier, **kwargs):
        if identifier != self.scenario.id:
            raise ValueError("Scenario outside snapshot")
        return self.scenario.model_copy(deep=True)

    def get_ruleset(self, version, **kwargs):
        if version != self.ruleset.version:
            raise ValueError("Ruleset outside snapshot")
        return self.ruleset.model_copy(deep=True)


class _Run:
    def __init__(self, snapshot, evaluated_at):
        self.snapshot = snapshot
        self.evaluated_at = evaluated_at
        self.today = evaluated_at.date()
        self.findings = []
        self.seen = set()
        self.values = {}
        self.stale = False

    def add(self, code, message, *, severity="verify", docs=(), fields=(), rule=None,
            origin="technical", action="Сверьте данные с оригиналом, уточните условие и повторите проверку."):
        item = Finding(
            code=code, severity=severity, message=message, next_action=action,
            document_ids=[doc.document_id for doc in docs], field_paths=list(fields),
            rule_ref=RuleRef(id=rule.id, version=rule.version) if rule else None,
            source_refs=rule.source_ids if rule else [], origin="rule" if rule else origin,
        )
        key = (item.code, tuple(item.document_ids), tuple(item.field_paths),
               rule.id if rule else None, item.message)
        if key not in self.seen:
            self.seen.add(key)
            self.findings.append(item)

    def value(self, doc, key):
        cache_key = (doc.document_id, key)
        if cache_key in self.values:
            return self.values[cache_key]
        found = [field for field in doc.fields if field.key == key]
        value = None
        if len(found) == 1:
            field = found[0]
            if field.user_confirmed and field.confirmed_revision == doc.facts_revision and isinstance(field.value, str):
                if key in AMOUNT_KEYS:
                    raw = field.value.strip()
                    value = raw if len(raw) <= 300 and re.fullmatch(r"\d+(?:\.\d+)?", raw) else None
                else:
                    value = normalize(key, field.value)
        if value is None:
            self.add("document_field_unverified", "Поле отсутствует, неоднозначно, невалидно или не подтверждено на текущей ревизии.",
                     docs=[doc], fields=[f"fields.{key}"])
        elif key in DATE_KEYS:
            value = date.fromisoformat(value)
        elif key in AMOUNT_KEYS:
            value = Decimal(value)
        self.values[cache_key] = value
        return value

    def general_checks(self):
        names = []
        profile = self.snapshot.profile
        expected_name = " ".join((profile.given_name or "", profile.surname or "")).strip() if profile.given_name and profile.surname else None
        for doc in self.snapshot.documents:
            for key in FIELD_KEYS[doc.document_type.value]:
                self.value(doc, key)
            if doc.document_type == "passport":
                given, surname = self.value(doc, "given_name"), self.value(doc, "surname")
                name = f"{given} {surname}" if given and surname else None
                issued, expiry, birth = (self.value(doc, key) for key in ("issued_on", "expires_on", "birth_date"))
                if issued and expiry and issued > expiry:
                    self.add("passport_date_order", "Дата выдачи позже окончания срока паспорта.", severity="fix", docs=[doc],
                             fields=["fields.issued_on", "fields.expires_on"], origin="heuristic")
                if birth and issued and birth > issued:
                    self.add("passport_birth_after_issue", "Дата рождения позже выдачи паспорта.", severity="fix", docs=[doc],
                             fields=["fields.birth_date", "fields.issued_on"], origin="heuristic")
                if issued and issued > self.today:
                    self.add("passport_issue_in_future", "Дата выдачи паспорта находится в будущем относительно проверки.",
                             docs=[doc], fields=["fields.issued_on"], origin="heuristic")
                if expiry and expiry < self.today:
                    self.add("passport_expired", "Указанный срок паспорта уже истёк.", severity="fix", docs=[doc],
                             fields=["fields.expires_on"], origin="heuristic", action="Проверьте дату или подготовьте действующий паспорт.")
                for key, profile_key in (("birth_date", "birth_date"), ("passport_kind", "passport_kind"),
                                         ("issued_on", "passport_issued_on"), ("expires_on", "passport_expires_on")):
                    actual, declared = self.value(doc, key), getattr(profile, profile_key)
                    if actual is not None and declared is not None and actual != declared:
                        self.add("profile_document_mismatch", "Подтверждённое поле паспорта отличается от профиля.",
                                 docs=[doc], fields=[f"fields.{key}", f"profile.{profile_key}"], origin="heuristic")
            else:
                name = self.value(doc, "holder_name")
                currency = self.value(doc, "currency")
                if currency is not None and currency not in CURRENCIES:
                    self.add("currency_unknown", "Валюта не поддерживается для автоматического сравнения.", docs=[doc], fields=["fields.currency"])
                if doc.document_type == "bank_statement":
                    statement = self.value(doc, "statement_on")
                    if statement and statement > self.today:
                        self.add("statement_in_future", "Дата выписки находится в будущем относительно проверки.", docs=[doc],
                                 fields=["fields.statement_on"], origin="heuristic")
                    self.add("statement_recency_manual", "Предельный возраст выписки не задан проверенным правилом; свежесть требует отдельной сверки.",
                             severity="info", docs=[doc], fields=["fields.statement_on"])
                else:
                    starts, ends = self.value(doc, "starts_on"), self.value(doc, "ends_on")
                    if starts and ends and starts > ends:
                        self.add("insurance_date_order", "Начало полиса позже его окончания.", severity="fix", docs=[doc],
                                 fields=["fields.starts_on", "fields.ends_on"], origin="heuristic")
                    self.add("policy_scope_limited", "Исключения договора, подлинность и действительность полиса у страховщика не проверены.",
                             severity="info", docs=[doc])
            if name:
                names.append((doc, name))
                if expected_name is None:
                    self.add("profile_name_unknown", "Для сверки владельца нужно заполнить имя и фамилию в профиле.",
                             docs=[doc], fields=["profile.given_name", "profile.surname"])
                elif text(name) != text(expected_name):
                    self.add("holder_mismatch", "Имя владельца отличается от профиля; порядок имён и транслитерацию нужно сверить вручную.",
                             docs=[doc], fields=["fields.holder_name"] if doc.document_type != "passport" else ["fields.given_name", "fields.surname"], origin="heuristic")
        for index, (left, left_name) in enumerate(names):
            for right, right_name in names[index + 1:]:
                if text(left_name) != text(right_name):
                    self.add("document_names_mismatch", "Подтверждённые имена в документах различаются; сверьте владельцев и транслитерацию.",
                             docs=[left, right], fields=["fields.given_name", "fields.surname", "fields.holder_name"], origin="heuristic")

    def compare_money(self, doc, key, minimum, rule):
        amount, currency = self.value(doc, key), self.value(doc, "currency")
        if amount is None or currency is None:
            return
        if currency not in CURRENCIES or minimum.currency not in CURRENCIES:
            self.add("money_currency_unknown", "Для денежного требования неизвестна или не поддерживается валюта.",
                     docs=[doc], fields=[f"fields.{key}", "fields.currency"], rule=rule)
        elif currency != minimum.currency:
            self.add("money_currency_mismatch", "Валюты различаются; подтверждённого курса нет, суммы не сравнивались.",
                     docs=[doc], fields=[f"fields.{key}", "fields.currency"], rule=rule)
        elif amount < Decimal(minimum.amount):
            self.add("insurance_cover_insufficient" if key == "coverage_amount" else "funds_insufficient",
                     f"Сумма ниже указанного в правиле минимума {minimum.amount} {minimum.currency}.",
                     severity="fix" if rule.mandatory else "info", docs=[doc], fields=[f"fields.{key}"], rule=rule)

    def rule_checks(self):
        snapshot = self.snapshot
        registry = _SnapshotRegistry(snapshot)
        evaluator = EligibilityService(registry)
        applicability = evaluator.evaluate_applicability(snapshot.profile, snapshot.case.trip, snapshot.case.answers,
                                                         snapshot.scenario, as_of=self.today)
        self.findings.extend(item.model_copy(deep=True) for item in [*applicability.barriers, *applicability.unknown_conditions])
        if applicability.missing_answers:
            self.add("check_inputs_missing", "Часть данных профиля, поездки или ответов неизвестна.", fields=applicability.missing_answers)
        if applicability.status == "unsupported_profile":
            return
        requirements = {item.id: item for item in applicability.requirements}
        rules = [rule for rule in snapshot.ruleset.rules if rule.scenario_id == snapshot.scenario.id]
        sources = {source.id: source for source in snapshot.scenario.sources}
        answers = validate_answers(snapshot.case.answers, {q.key: q for q in snapshot.scenario.questions})
        context = {"profile": snapshot.profile.model_dump(), "trip": snapshot.case.trip.model_dump(), "answers": answers}
        for requirement in requirements.values():
            if requirement.document_type is None:
                continue
            if requirement.applicability == "unknown":
                self.add("document_requirement_unknown", "Применимость документного требования не подтверждена.", fields=[f"requirements.{requirement.id}"])
            elif requirement.applicability == "yes" and requirement.mandatory and not any(
                doc.document_type == requirement.document_type for doc in snapshot.documents
            ):
                rule = next((rule for rule in rules if rule.requirement_id == requirement.id and rule.kind == "requirement"), None)
                rule = rule or next((rule for rule in rules if rule.requirement_id == requirement.id), None)
                self.add("required_document_missing", f"Не прикреплён документ: {requirement.title}.", severity="fix",
                         fields=[f"requirements.{requirement.id}"], rule=rule)
        for rule in rules:
            params = parse_parameters(rule)  # Exactly the bounded registry of package 2.
            if not isinstance(params, (PassportValidityParameters, PassportMaxAgeParameters, InsuranceParameters, FundsParameters)):
                continue  # Assertions/stays/manual conditions were evaluated by package 3 above.
            requirement = requirements.get(rule.requirement_id)
            if requirement is None or requirement.applicability == "no":
                continue
            if (requirement.applicability != "yes" or not rule.source_ids or any(
                source_id not in sources or source_quality(sources[source_id], self.today) != "source_checked"
                for source_id in rule.source_ids
            )):
                self.add("document_rule_unverified", "Документное правило или его источник требуют проверки.", rule=rule)
                continue
            trace = evaluate_condition(rule.applies_if, context)
            if trace.value == "no":
                continue
            if trace.value == "unknown":
                self.add("document_rule_inputs_missing", "Неизвестны условия применимости документного правила.", fields=sorted(trace.missing), rule=rule)
                continue
            if isinstance(params, FundsParameters):
                documents = [doc for doc in snapshot.documents if doc.document_type == "bank_statement"]
                if params.minimum is None or params.period != "trip":
                    self.add("funds_manual_calculation", "Финансовое требование не имеет подтверждённого полного порога на поездку; нужен ручной расчёт.",
                             docs=documents, fields=["fields.balance", "fields.currency"], rule=rule)
                    continue
                if not documents:
                    self.add("funds_evidence_manual", "Нужно сопоставить финансовое требование с выбранным доказательством или спонсором.", rule=rule)
            else:
                documents = [doc for doc in snapshot.documents if doc.document_type == requirement.document_type]
            for doc in documents:
                try:
                    if isinstance(params, (PassportValidityParameters, PassportMaxAgeParameters)):
                        self.passport_rule(doc, rule, params, context)
                    elif isinstance(params, InsuranceParameters):
                        self.insurance_rule(doc, rule, params)
                    elif isinstance(params, FundsParameters):
                        self.compare_money(doc, "balance", params.minimum, rule)
                except (ValueError, OverflowError):
                    self.add("date_out_of_range", "Календарный расчёт выходит за поддерживаемый диапазон дат.", docs=[doc], rule=rule)

    def passport_rule(self, doc, rule, params, context):
        if doc.document_type != "passport":
            self.add("rule_document_type_mismatch", "Паспортное правило связано с другим типом документа.", rule=rule)
            return
        anchor = lookup(context, params.anchor)
        if not isinstance(anchor, date):
            self.add("document_rule_inputs_missing", "Неизвестна опорная дата паспортного правила.", docs=[doc], fields=[params.anchor], rule=rule)
            return
        if isinstance(params, PassportValidityParameters):
            expires = self.value(doc, "expires_on")
            threshold = add_calendar_months(anchor, params.months) + timedelta(days=params.days)
            if expires is not None and expires < threshold:
                self.add("passport_validity", f"Подтверждённый срок паспорта меньше требуемого: {threshold.isoformat()}.",
                         severity=params.severity if rule.mandatory else "info", docs=[doc],
                         fields=["fields.expires_on", params.anchor], rule=rule,
                         action="Проверьте срок паспорта или измените даты поездки.")
        else:
            issued = self.value(doc, "issued_on")
            threshold = add_calendar_months(anchor, -12 * params.years)
            if issued is not None and (issued < threshold or (issued == threshold and not params.allow_anniversary)):
                self.add("passport_age", "Подтверждённая дата выдачи выходит за разрешённый календарный срок.",
                         severity="blocking" if rule.mandatory else "info", docs=[doc], fields=["fields.issued_on", params.anchor], rule=rule)
            elif issued is not None and issued > anchor:
                self.add("passport_issue_after_anchor", "Паспорт выдан позже опорной даты правила.", severity="fix",
                         docs=[doc], fields=["fields.issued_on", params.anchor], rule=rule)

    def insurance_rule(self, doc, rule, params):
        if doc.document_type != "insurance":
            self.add("rule_document_type_mismatch", "Правило покрытия связано с другим типом документа.", rule=rule)
            return
        trip = self.snapshot.case.trip
        if params.covers_trip:
            start, end = self.value(doc, "starts_on"), self.value(doc, "ends_on")
            if trip.starts_on is None or trip.ends_on is None:
                self.add("insurance_trip_unknown", "Для проверки покрытия нужны обе даты поездки.", docs=[doc], fields=["trip.starts_on", "trip.ends_on"], rule=rule)
            elif start is not None and end is not None and (start > trip.starts_on or end < trip.ends_on):
                self.add("insurance_dates_not_covered", "Подтверждённые даты полиса не покрывают всю поездку.",
                         severity="fix" if rule.mandatory else "info", docs=[doc],
                         fields=["fields.starts_on", "fields.ends_on", "trip.starts_on", "trip.ends_on"], rule=rule)
        territory = self.value(doc, "territory")
        if territory is not None:
            actual, required = " ".join(territory.upper().split()), params.territory.upper()
            actual = COUNTRY_LABELS.get(actual, actual)
            required = COUNTRY_LABELS.get(required, required)
            if actual != required:
                known_country = actual in set(COUNTRY_LABELS.values())
                self.add("insurance_territory_mismatch" if known_country else "insurance_territory_unknown",
                         "Территория полиса отличается от требуемой; сложные формулировки и исключения нужно сверять вручную.",
                         severity="fix" if known_country and rule.mandatory else "verify", docs=[doc], fields=["fields.territory"], rule=rule)
        if params.minimum_cover is not None:
            self.compare_money(doc, "coverage_amount", params.minimum_cover, rule)

    def result(self):
        return CheckResult(id=uuid4(), case_id=self.snapshot.case.id, evaluated_at=self.evaluated_at,
                           input_signature=signature(self.snapshot), findings=self.findings, stale=self.stale)


def check_case(snapshot: CaseSnapshot, *, evaluated_at: datetime | None = None) -> CheckResult:
    timestamp = evaluated_at or datetime.now(timezone.utc)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("An aware evaluation timestamp is required")
    run = _Run(snapshot, timestamp.astimezone(timezone.utc))
    expected = signature(snapshot)
    supplied = snapshot.input_signature
    if (snapshot.case.scenario_id != snapshot.scenario.id or snapshot.case.ruleset_version != snapshot.ruleset.version
        or snapshot.scenario.version != snapshot.ruleset.version
        or len({doc.document_id for doc in snapshot.documents}) != len(snapshot.documents)
        or set(snapshot.case.attached_document_ids) != set(expected.facts_revisions)
        or any(getattr(supplied, key) != getattr(expected, key) for key in
               ("case_revision", "profile_revision", "facts_revisions", "ruleset_version"))):
        run.stale = True
        run.add("snapshot_mismatch", "Версии, документы или сценарий снимка не согласованы; получите новый снимок.")
        return run.result()
    run.general_checks()
    try:
        run.rule_checks()
    except (ValueError, ValidationError, AppError):
        run.stale = True
        run.add("snapshot_rules_invalid", "Входы или правила снимка не прошли валидацию пакетов 2/3.")
    return run.result()


class DocumentChecker:
    algorithm_version = ALGORITHM_VERSION

    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def check_case(self, snapshot: CaseSnapshot) -> CheckResult:
        return check_case(snapshot, evaluated_at=self.clock())
