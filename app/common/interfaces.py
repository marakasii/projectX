"""Feature ports: signatures only. Their owners supply implementations."""

from typing import Protocol
from uuid import UUID

from fastapi import Request
from pydantic import JsonValue

from app.common.models import (
    AIResult, Answers, ApplicantProfile, ApplicabilityResult, Case,
    CaseSnapshot, CheckResult, ConfirmedDocumentFacts, Document, Event,
    LetterDraft, Process, ProcessStageTemplate, Route, RouteResult, Ruleset,
    Scenario, Source, TripInput, UserContext,
)


class AuthGateway(Protocol):
    async def require_user(self, request: Request) -> UserContext: ...

    async def require_csrf(self, request: Request) -> UserContext: ...


class RuleRegistry(Protocol):
    def get_scenario(self, scenario_id: str, *, version: str | None = None) -> Scenario: ...

    def get_ruleset(self, version: str) -> Ruleset: ...

    def get_sources(self, source_ids: list[str], *, version: str | None = None) -> list[Source]: ...


class ApplicabilityEvaluator(Protocol):
    def evaluate_applicability(
        self, profile: ApplicantProfile, trip: TripInput,
        answers: Answers, scenario: Scenario,
    ) -> ApplicabilityResult: ...


class CaseRebuilder(Protocol):
    def rebuild_case(self, owner: UserContext, case_id: UUID, reason: str) -> Case: ...


class DocumentService(Protocol):
    def get_confirmed_facts(
        self, owner_id: UUID, document_ids: list[UUID],
    ) -> list[ConfirmedDocumentFacts]: ...

    async def extract_document(self, owner_id: UUID, document_id: UUID) -> Document: ...


class AIProvider(Protocol):
    async def generate(
        self, task: str, facts: dict[str, JsonValue],
        output_schema: dict[str, JsonValue],
    ) -> AIResult: ...


class CaseChecker(Protocol):
    def check_case(self, snapshot: CaseSnapshot) -> CheckResult: ...


class RouteChecker(Protocol):
    def check_route(
        self, profile: ApplicantProfile, route: Route, ruleset: Ruleset,
    ) -> RouteResult: ...


class LetterGenerator(Protocol):
    async def generate_cover_letter(self, confirmed_snapshot: CaseSnapshot) -> LetterDraft: ...


class ProcessProjector(Protocol):
    def project_process(
        self, events: list[Event], process_template: list[ProcessStageTemplate],
        *, context: dict | None = None, unverified_source_ids: list[str] | tuple[str, ...] = (),
    ) -> Process: ...
