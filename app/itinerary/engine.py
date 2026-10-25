"""Deterministic, partial transit checks; no flight lookup, AI or network."""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.common.dates import add_calendar_months
from app.common.models import DataQuality, Finding, Route, RouteResult, RuleRef
from app.rules.registry import source_quality
from .schema import parse_parameters, validate_ruleset

ALGORITHM_VERSION = "transit-1"
# Engineering boundary of automation, not a legal maximum transit duration.
AUTOMATED_CONNECTION_LIMIT = timedelta(hours=24)


def utc(value):
    try:
        return value.astimezone(timezone.utc)
    except OverflowError as exc:
        raise ValueError("Дата выходит за поддерживаемый диапазон UTC.") from exc


def validate_route(route: Route):
    """Allow incomplete facts; reject impossible chronology and malformed codes."""
    if len(route.legs) > 24 or len(route.transfers) > 24 or len(route.model_dump_json().encode("utf-8")) > 65536:
        raise ValueError("Маршрут превышает лимит: 24 участка/пересадки, 64 KiB.")
    for leg in route.legs:
        for timestamp in (leg.departure_at, leg.arrival_at):
            if timestamp is not None:
                utc(timestamp)
        for airport in (leg.departure_airport, leg.arrival_airport):
            if airport is not None and not re.fullmatch(r"[A-Z]{3}", airport):
                raise ValueError("Аэропорт задаётся трёхбуквенным IATA-кодом в верхнем регистре.")
        if leg.departure_at and leg.arrival_at and utc(leg.arrival_at) <= utc(leg.departure_at):
            raise ValueError("Прибытие участка должно быть позже вылета.")
    for previous, following in zip(route.legs, route.legs[1:]):
        if previous.arrival_at and following.departure_at and utc(following.departure_at) <= utc(previous.arrival_at):
            raise ValueError("Следующий вылет должен быть позже предыдущего прибытия.")
    for transfer in route.transfers:
        for timestamp in (transfer.arrival_at, transfer.departure_at):
            if timestamp is not None:
                utc(timestamp)
        if transfer.airport is not None and not re.fullmatch(r"[A-Z]{3}", transfer.airport):
            raise ValueError("Аэропорт пересадки задаётся IATA-кодом в верхнем регистре.")
        if transfer.arrival_at and transfer.departure_at and utc(transfer.departure_at) <= utc(transfer.arrival_at):
            raise ValueError("Вылет пересадки должен быть позже прибытия.")


class _Run:
    def __init__(self, profile, route, ruleset, evaluated_at):
        self.profile, self.route, self.ruleset = profile, route, ruleset
        self.today = utc(evaluated_at).date()
        self.findings = []
        self.coverage = {}  # Country -> all its transfers have applicable checked rules.
        self.sources = {source.id: source for source in ruleset.sources}

    def add(self, code, message, action, *, paths=(), severity="verify", rule=None):
        self.findings.append(Finding(
            code=code, severity=severity, message=message, next_action=action, field_paths=list(paths),
            origin="rule" if rule else "heuristic",
            rule_ref=RuleRef(id=rule.id, version=rule.version) if rule else None,
            source_refs=rule.source_ids if rule else [],
        ))

    def rule(self, country, kind, airport):
        candidates = [rule for rule in self.ruleset.rules
                      if rule.kind == kind and parse_parameters(rule).country == country
                      and airport in parse_parameters(rule).airports]
        if not candidates:
            return None
        rule = candidates[0]
        if any(source_quality(self.sources[source_id], self.today) != DataQuality.SOURCE_CHECKED
               for source_id in rule.source_ids):
            self.add("transit_source_unverified", "Источник транзитного правила требует перепроверки.",
                     "Откройте официальный источник и обновите версию правил.", rule=rule)
            return None
        return rule

    def scope(self):
        profile = self.profile
        dates = [leg.departure_at for leg in self.route.legs if leg.departure_at]
        reference = utc(min(dates)).date() if dates else self.today
        birth = profile.birth_date
        adult = None if birth is None else (reference.year - birth.year - ((reference.month, reference.day) < (birth.month, birth.day))) >= 18
        required = {
            "citizenship": profile.citizenship == "RU" if profile.citizenship is not None else None,
            "residence_country": profile.residence_country == "RU" if profile.residence_country is not None else None,
            "has_other_citizenship": None if profile.has_other_citizenship is None else not profile.has_other_citizenship,
            "has_foreign_residence_permit": None if profile.has_foreign_residence_permit is None else not profile.has_foreign_residence_permit,
            "passport_kind": profile.passport_kind in ("biometric", "non_biometric") if profile.passport_kind is not None else None,
            "birth_date": adult,
        }
        missing = ["profile." + key for key, value in required.items() if value is None]
        unsupported = ["profile." + key for key, value in required.items() if value is False]
        if missing or unsupported:
            self.add("transit_profile_scope", "Профиль не подтверждает поддерживаемый случай взрослого туриста с паспортом РФ.",
                     "Заполните профиль или проверьте условия индивидуально.", paths=missing + unsupported)
            return False
        return True

    def structure(self):
        route = self.route
        complete = True
        if not route.legs:
            self.add("route_legs_missing", "Участки маршрута не указаны.", "Добавьте все участки перелёта.", paths=["route.legs"])
            complete = False
        for index, leg in enumerate(route.legs):
            missing = [f"route.legs.{index}.{key}" for key, value in leg.model_dump().items() if value is None]
            if missing:
                self.add("route_leg_incomplete", "Для участка маршрута не хватает данных.",
                         "Укажите страны, аэропорты и время с часовым поясом.", paths=missing)
                complete = False
        for index, (before, after) in enumerate(zip(route.legs, route.legs[1:])):
            transfer = route.transfers[index] if index < len(route.transfers) else None
            for country in (before.arrival_country, after.departure_country):
                if country and (transfer is None or country != transfer.country):
                    self.coverage[country] = False
        expected = max(0, len(route.legs) - 1)
        if len(route.transfers) != expected:
            self.add("route_connections_incomplete", "Количество пересадок не соответствует стыкам участков.",
                     "Опишите каждый стык соседних участков в том же порядке.", paths=["route.legs", "route.transfers"])
            complete = False
            for leg in route.legs[:-1]:
                if leg.arrival_country:
                    self.coverage[leg.arrival_country] = False
        return complete

    def transfer(self, index, transfer, supported, structure_complete):
        prefix = f"route.transfers.{index}"
        complete = structure_complete
        missing = [f"{prefix}.{key}" for key in ("airport", "arrival_at", "departure_at", "ticket_type", "leaves_airside", "rechecks_baggage")
                   if getattr(transfer, key) is None]
        if missing:
            self.add("transfer_facts_unknown", "Условия пересадки неизвестны.",
                     "Уточните время, билет, выход из зоны и получение багажа у перевозчика.", paths=missing)
            complete = False
        airport_change = domestic = entry_location_confirmed = False
        if index + 1 < len(self.route.legs):
            before, after = self.route.legs[index:index + 2]
            mismatch = []
            for key, values in {
                "country": (transfer.country, before.arrival_country, after.departure_country),
                "airport": (transfer.airport, before.arrival_airport, after.departure_airport),
                "arrival_at": (transfer.arrival_at, before.arrival_at),
                "departure_at": (transfer.departure_at, after.departure_at),
            }.items():
                if all(value is not None for value in values) and len(set(values)) > 1:
                    mismatch.append(prefix + "." + key)
            entry_location_confirmed = (
                before.arrival_country == after.departure_country == transfer.country
                and before.arrival_airport == transfer.airport and transfer.airport is not None
                and before.arrival_at is not None and transfer.arrival_at == before.arrival_at
            )
            airport_change = bool(before.arrival_airport and after.departure_airport
                                  and before.arrival_airport != after.departure_airport)
            domestic = before.departure_country == transfer.country or after.arrival_country == transfer.country
            if mismatch:
                self.add("transfer_leg_mismatch", "Пересадка расходится с соседними участками или требует смены аэропорта.",
                         "Сверьте маршрут; при смене аэропорта проверьте условия въезда.", paths=mismatch)
                complete = False
        else:
            complete = False
        requires_entry = transfer.leaves_airside is True or transfer.rechecks_baggage is True or airport_change or domestic
        if transfer.leaves_airside is False and requires_entry:
            self.add("transfer_airside_conflict", "Заявлено нахождение в зоне, но багаж, внутренний участок или смена аэропорта требуют уточнения въезда.",
                     "Уточните прохождение паспортного контроля.", paths=[prefix + ".leaves_airside"])
            complete = False
        if transfer.ticket_type == "separate":
            self.add("separate_tickets_unverified", "Отдельные билеты требуют проверки регистрации и передачи багажа.",
                     "Подтвердите процедуру у обоих перевозчиков; единая пересадка не предполагается.", paths=[prefix + ".ticket_type"])
            complete = False
        if transfer.arrival_at and transfer.departure_at:
            if utc(transfer.departure_at) - utc(transfer.arrival_at) >= AUTOMATED_CONNECTION_LIMIT:
                self.add("long_connection_uncovered", "Пересадка от 24 часов выходит за границу автоматической проверки демо.",
                         "Уточните режим ожидания и въезда; 24 часа здесь не юридический лимит.", paths=[prefix + ".departure_at"])
                complete = False
        if not supported:
            return False
        country, airport = transfer.country, transfer.airport
        operations = self.rule(country, "transit_operations", airport)
        if operations and (transfer.ticket_type == "separate" or airport_change):
            self.add("dxb_transfer_procedure", parse_parameters(operations).message,
                     "Подтвердите паспортный контроль и схему багажа у перевозчика.", paths=[prefix], rule=operations)
        relevant = [permission for permission in [*(self.profile.existing_permissions or []), *(transfer.permissions or [])]
                    if permission.country == country]
        if relevant:
            self.add("transit_permissions_not_validated", "Имеющееся разрешение не проверено по типу, срокам начала, числу въездов и условиям использования.",
                     "При необходимости въезда проверьте разрешение отдельно; оно не отменяет замечаний.",
                     paths=[prefix + ".permissions", "profile.existing_permissions"], severity="verify" if requires_entry else "info")
        if requires_entry:
            return self.entry(transfer, prefix, allow_block=transfer.leaves_airside is True and entry_location_confirmed)
        rule = self.rule(country, "transit_airside", airport)
        if rule is None:
            self.add("transit_scope_uncovered", "Страна, аэропорт или условия этой пересадки не покрыты проверенным правилом.",
                     "Проверьте официальный транзитный режим и процедуру перевозчика.", paths=[prefix])
            return False
        if not complete:
            return False
        self.add("airside_visa_not_required", "В покрытом случае без выхода из международной зоны транзитная виза не требуется.",
                 "Подтвердите у перевозчика терминалы, посадочный талон, багаж и достаточность времени пересадки.",
                 paths=[prefix], severity="info", rule=rule)
        return True

    def entry(self, transfer, prefix, *, allow_block):
        rule = self.rule(transfer.country, "transit_entry_ru", transfer.airport)
        if rule is not None:
            parameters = parse_parameters(rule)
            self.add("entry_history_unverified", "Найден безвизовый въезд/транзит для РФ, но лимит пребываний 90/180 и остальные условия въезда не проверены.",
                     "Проверьте предыдущие пребывания и въезд у официального органа/перевозчика.", paths=[prefix], rule=rule)
            if transfer.arrival_at is not None and self.profile.passport_expires_on is not None:
                try:
                    arrival = transfer.arrival_at.astimezone(ZoneInfo("Asia/Dubai")).date()
                    minimum = add_calendar_months(arrival, parameters.passport_months)
                except (ValueError, OverflowError):
                    self.add("transit_date_out_of_range", "Не удалось рассчитать срок действия паспорта.",
                             "Проверьте даты вручную.", paths=[prefix + ".arrival_at"])
                else:
                    if allow_block and self.profile.passport_expires_on < minimum:
                        self.add("ae_entry_passport_validity", f"Для покрытого въезда в ОАЭ паспорт должен действовать минимум до {minimum.isoformat()}.",
                                 "Обновите паспорт или измените маршрут и перепроверьте правила въезда.", severity="blocking",
                                 paths=["profile.passport_expires_on", prefix + ".arrival_at"], rule=rule)
            return False  # No prior-stay history / entry clearance in the shared input.
        context = self.rule(transfer.country, "transit_entry_context", transfer.airport)
        self.add("entry_conditions_uncovered", parse_parameters(context).message if context else "Выход из зоны или наземный транзит не покрыт этим набором правил.",
                 "Проверьте право въезда; наличие общего визового режима не закрывает все условия пересадки.",
                 paths=[prefix], rule=context)
        return False

    def run(self):
        supported, complete = self.scope(), self.structure()
        expiry = self.profile.passport_expires_on
        travel_dates = [utc(leg.departure_at).date() for leg in self.route.legs if leg.departure_at]
        travel_dates += [utc(leg.arrival_at).date() for leg in self.route.legs if leg.arrival_at]
        if expiry is None or (travel_dates and expiry < max(travel_dates)):
            self.add("transit_passport_unverified", "Срок действия паспорта не подтверждён для всех дат маршрута.",
                     "Уточните или обновите паспортные данные.", paths=["profile.passport_expires_on"])
        for index, transfer in enumerate(self.route.transfers):
            covered = self.transfer(index, transfer, supported, complete)
            self.coverage[transfer.country] = self.coverage.get(transfer.country, True) and covered
        self.add("route_scope_limited", "Проверка охватывает только указанные транзитные условия. Наличие рейсов и допуск к перевозке не проверены.",
                 "Проверьте расписание, терминалы, регистрацию, минимальное время и условия назначения у перевозчика.", severity="info")
        status = ("blocked" if any(f.severity == "blocking" for f in self.findings) else
                  "needs_verification" if any(f.severity in ("verify", "fix") for f in self.findings) else "no_known_blockers")
        return RouteResult(status=status, findings=self.findings,
                           covered_countries=sorted(c for c, covered in self.coverage.items() if covered),
                           uncovered_countries=sorted(c for c, covered in self.coverage.items() if not covered))


def check_route(profile, route, ruleset, *, evaluated_at=None) -> RouteResult:
    validate_route(route)
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("Evaluation clock must have a time zone")
    try:
        validate_ruleset(ruleset)
    except ValueError:
        return RouteResult(status="needs_verification", covered_countries=[],
                           uncovered_countries=sorted({transfer.country for transfer in route.transfers}), findings=[Finding(
                               code="transit_rules_invalid", severity="verify", origin="technical",
                               message="Транзитные правила не прошли проверку схемы.",
                               next_action="Подключите корректную версию правил и повторите проверку.")])
    return _Run(profile, route, ruleset, evaluated_at).run()


class TransitChecker:
    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def check_route(self, profile, route, ruleset) -> RouteResult:
        return check_route(profile, route, ruleset, evaluated_at=self.clock())
