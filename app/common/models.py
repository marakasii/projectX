"""Public and inter-package Pydantic contracts. No rules or projections run here."""

from datetime import date
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field as PydanticField,
    HttpUrl, JsonValue, SecretStr, StrictBool, model_validator,
)

Revision = Annotated[int, PydanticField(strict=True, ge=1)]
CountryCode = Annotated[str, PydanticField(pattern=r"^[A-Z]{2}$")]
CurrencyCode = Annotated[str, PydanticField(pattern=r"^[A-Z]{3}$")]
Amount = Annotated[str, PydanticField(pattern=r"^\d+(\.\d+)?$")]
Answers = dict[str, JsonValue]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


T = TypeVar("T")


class Items(Model, Generic[T]):
    items: list[T]


class DocumentType(StrEnum):
    PASSPORT = "passport"
    BANK_STATEMENT = "bank_statement"
    INSURANCE = "insurance"


class ExtractionState(StrEnum):
    UPLOADED = "uploaded"
    EXTRACTED = "extracted"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class Severity(StrEnum):
    BLOCKING = "blocking"
    FIX = "fix"
    VERIFY = "verify"
    INFO = "info"


class EntryMode(StrEnum):
    VISA = "visa"
    EVISA = "evisa"
    VISA_FREE = "visa_free"
    NEEDS_VERIFICATION = "needs_verification"


class DataQuality(StrEnum):
    SOURCE_CHECKED = "source_checked"
    NEEDS_VERIFICATION = "needs_verification"
    CONFLICTING = "conflicting"


class Applicability(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class ChecklistKind(StrEnum):
    DOCUMENT = "document"
    PREPARATION_ACTION = "preparation_action"
    PROCESS_ACTION = "process_action"


class ChecklistState(StrEnum):
    MISSING = "missing"
    RECEIVED = "received"
    ISSUES = "issues"
    NEEDS_VERIFICATION = "needs_verification"
    SATISFIED = "satisfied"
    NOT_APPLICABLE = "not_applicable"


class Outcome(StrEnum):
    UNKNOWN = "unknown"
    APPROVED = "approved"
    REFUSED = "refused"
    NOT_APPLICABLE = "not_applicable"


class EventType(StrEnum):
    FORM_PREPARED = "form_prepared"
    APPOINTMENT_BOOKED = "appointment_booked"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    ADDITIONAL_DOCUMENTS_REQUESTED = "additional_documents_requested"
    DECISION_RECEIVED = "decision_received"
    PASSPORT_RETURNED = "passport_returned"
    ENTRY_PREPARATION_COMPLETED = "entry_preparation_completed"


class Error(Model):
    code: str
    message: str
    details: dict[str, JsonValue] = PydanticField(default_factory=dict)


class ErrorResponse(Model):
    error: Error


class Health(Model):
    status: Literal["ok"]
    demo_mode: bool
    database: Literal["ok"]
    schema_version: int


class User(Model):
    id: UUID
    username: str
    created_at: AwareDatetime


class Credentials(Model):
    username: Annotated[str, PydanticField(
        min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"
    )]
    password: Annotated[SecretStr, PydanticField(min_length=8, max_length=128)]


class SessionResponse(Model):
    user: User
    csrf_token: str


class Money(Model):
    amount: Amount
    currency: CurrencyCode | None = None


class ExistingPermission(Model):
    country: CountryCode
    kind: str
    expires_on: date | None = None


class ProfileInput(Model):
    given_name: str | None = None
    surname: str | None = None
    birth_date: date | None = None
    citizenship: CountryCode | None = None
    residence_country: CountryCode | None = None
    has_other_citizenship: StrictBool | None = None
    has_foreign_residence_permit: StrictBool | None = None
    passport_kind: Literal["biometric", "non_biometric"] | None = None
    passport_issued_on: date | None = None
    passport_expires_on: date | None = None
    employment_status: Literal["employed", "self_employed", "student", "retired", "unemployed", "other"] | None = None
    existing_permissions: list[ExistingPermission] | None = None


class ApplicantProfile(ProfileInput):
    id: UUID
    revision: Revision


class TripInput(Model):
    purpose: str | None = None
    destination_country: CountryCode | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    departure_city: str | None = None
    application_country: CountryCode | None = None
    accommodation_type: Literal["hotel", "host", "rental", "other"] | None = None
    funding_type: Literal["self", "sponsor", "mixed", "other"] | None = None

    @model_validator(mode="after")
    def ordered_dates(self):
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("ends_on must not precede starts_on")
        return self


class Source(Model):
    id: str
    url: HttpUrl
    authority: str
    checked_at: AwareDatetime | None = None
    review_after: date | None = None
    status: DataQuality = DataQuality.NEEDS_VERIFICATION
    attempted_at: AwareDatetime | None = None
    locator: str | None = None
    evidence: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def checked_source_has_evidence(self):
        if self.status == DataQuality.SOURCE_CHECKED:
            if self.checked_at is None or self.review_after is None or not self.evidence:
                raise ValueError("Checked sources need checked_at, review_after and evidence")
        if self.checked_at and self.review_after and self.review_after < self.checked_at.date():
            raise ValueError("review_after precedes checked_at")
        return self


class RuleRef(Model):
    id: str
    version: str


class Finding(Model):
    code: str
    severity: Severity
    message: str
    next_action: str
    document_ids: list[UUID] = PydanticField(default_factory=list)
    field_paths: list[str] = PydanticField(default_factory=list)
    rule_ref: RuleRef | None = None
    source_refs: list[str] = PydanticField(default_factory=list)
    origin: Literal["rule", "heuristic", "technical"]
    resolved: bool = False


class ComparisonCondition(Model):
    field: str
    op: Literal["eq", "in", "exists", "gte", "lte"]
    value: JsonValue = None


class AllCondition(Model):
    all: list["ComparisonCondition | AllCondition | AnyCondition"]


class AnyCondition(Model):
    any: list["ComparisonCondition | AllCondition | AnyCondition"]


Condition = ComparisonCondition | AllCondition | AnyCondition
AllCondition.model_rebuild()
AnyCondition.model_rebuild()


class Requirement(Model):
    id: str
    title: str
    kind: ChecklistKind
    mandatory: bool
    readiness_contributing: bool
    document_type: DocumentType | None = None
    applies_if: Condition | None = None
    source_ids: list[str] = PydanticField(default_factory=list)


class Rule(Model):
    id: str
    version: str
    scenario_id: str
    # Package 2 defines the bounded registry of kind/parameter combinations.
    kind: str
    applies_if: Condition | None = None
    requirement_id: str | None = None
    parameters: dict[str, JsonValue] = PydanticField(default_factory=dict)
    mandatory: bool
    source_ids: list[str]


class ProcessStageTemplate(Model):
    id: str
    title: str
    event_types: list[EventType]
    applies_if: Condition | None = None
    source_ids: list[str] = PydanticField(default_factory=list)


class Question(Model):
    key: Annotated[str, PydanticField(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    title: str
    value_type: Literal["boolean", "integer", "date", "enum", "stays"]
    choices: list[str] = PydanticField(default_factory=list)
    minimum: Annotated[int, PydanticField(strict=True)] | None = None
    maximum: Annotated[int, PydanticField(strict=True)] | None = None
    source_ids: list[str]

    @model_validator(mode="after")
    def valid_constraints(self):
        if self.value_type == "enum":
            if not self.choices or len(self.choices) != len(set(self.choices)):
                raise ValueError("Enum questions require unique choices")
        elif self.choices:
            raise ValueError("Only enum questions have choices")
        if self.value_type != "integer" and (self.minimum is not None or self.maximum is not None):
            raise ValueError("Only integer questions have bounds")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Question bounds are reversed")
        return self


class AppliedRequirement(Requirement):
    applicability: Applicability
    explanation: str
    rule_refs: list[RuleRef] = PydanticField(default_factory=list)


class Scenario(Model):
    id: str
    destination_country: CountryCode
    version: str
    entry_mode: EntryMode = EntryMode.NEEDS_VERIFICATION
    supported_profile: dict[str, JsonValue]
    requirements: list[Requirement]
    process_stages: list[ProcessStageTemplate]
    sources: list[Source]
    data_quality: DataQuality = DataQuality.NEEDS_VERIFICATION
    entry_source_ids: list[str] = PydanticField(default_factory=list)
    questions: list[Question] = PydanticField(default_factory=list)
    coverage: Literal["partial", "complete"] = "partial"
    unverified_conditions: list[str] = PydanticField(default_factory=list)


class Ruleset(Model):
    version: str
    rules: list[Rule]
    sources: list[Source]


class Destination(Model):
    country: CountryCode
    name: str
    scenario_id: str
    version: str
    entry_mode: EntryMode
    data_quality: DataQuality


class ApplicabilityResult(Model):
    status: Literal["applicable", "blocked", "needs_input", "unsupported_profile", "needs_verification"]
    requirements: list[AppliedRequirement]
    barriers: list[Finding]
    missing_answers: list[str]
    unknown_conditions: list[Finding] = PydanticField(default_factory=list)


class ComparisonInput(Model):
    trip: TripInput
    answers: Answers = PydanticField(default_factory=dict)


class DestinationComparison(Model):
    destination: Destination
    profile_revision: Revision
    applicability: ApplicabilityResult
    required_documents: int | None = None
    external_actions: list[str]
    route_notes: list[str]
    unknown_conditions: list[str]
    known_required_documents: int = 0
    requirements_complete: bool = False


class Field(Model):
    key: str
    value: JsonValue
    confidence: Annotated[float, PydanticField(ge=0, le=1)] | None = None
    source_page: Annotated[int, PydanticField(ge=1)] | None = None
    source_kind: Literal["pdf_text", "ocr", "manual"]
    user_confirmed: StrictBool = False
    confirmed_revision: Revision | None = None


class Document(Model):
    id: UUID
    document_type: DocumentType
    original_filename: str
    mime_type: Literal["application/pdf", "image/jpeg", "image/png"]
    size_bytes: Annotated[int, PydanticField(gt=0)]
    sha256: Annotated[str, PydanticField(pattern=r"^[a-f0-9]{64}$")]
    extraction_state: ExtractionState
    facts_revision: Revision
    fields: list[Field]
    quality_findings: list[Finding]
    created_at: AwareDatetime


class FieldUpdate(Model):
    key: str
    value: JsonValue
    user_confirmed: StrictBool


class DocumentFieldsPatch(Model):
    expected_facts_revision: Revision
    fields: list[FieldUpdate]


class ChecklistItem(Model):
    id: str
    requirement_id: str
    title: str = ""
    kind: ChecklistKind
    mandatory: bool
    applicability: Applicability
    readiness_contributing: bool
    state: ChecklistState
    document_ids: list[UUID]
    verification_method: Literal["rules_checked", "user_confirmed"] | None = None
    findings: list[Finding]
    explanation: str
    source_refs: list[str]
    next_action: str | None = None


class Readiness(Model):
    percent: Annotated[int, PydanticField(ge=0, le=100)] | None = None
    required_total: Annotated[int, PydanticField(ge=0)] = 0
    satisfied_required: Annotated[int, PydanticField(ge=0)] = 0
    ready: bool = False
    unknown_required_conditions: list[str] = PydanticField(default_factory=list)
    blocking_count: Annotated[int, PydanticField(ge=0)] = 0
    reasons: list[str] = PydanticField(default_factory=lambda: ["needs_verification"])


class ProcessStage(Model):
    id: str
    title: str
    applicability: Applicability
    completed: bool
    event_ids: list[UUID]


class Process(Model):
    percent: Annotated[int, PydanticField(ge=0, le=100)] | None = None
    stages: list[ProcessStage] = PydanticField(default_factory=list)
    current_stage: str | None = None
    outcome: Outcome = Outcome.UNKNOWN


class CaseCreate(Model):
    scenario_id: str
    trip: TripInput
    answers: Answers = PydanticField(default_factory=dict)


class CasePatch(Model):
    trip: TripInput | None = None
    answers: Answers | None = None


class ChecklistPatch(Model):
    confirmed: StrictBool


class Case(Model):
    id: UUID
    scenario_id: str
    trip: TripInput
    answers: Answers
    revision: Revision
    profile_revision: Revision
    ruleset_version: str
    attached_document_ids: list[UUID]
    checklist: list[ChecklistItem]
    readiness: Readiness
    process: Process
    applicability: ApplicabilityResult | None = None
    stale: bool
    stale_reason: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class InputSignature(Model):
    case_revision: Revision
    profile_revision: Revision
    facts_revisions: dict[UUID, Revision]
    ruleset_version: str
    algorithm_version: str
    route_revision: Revision | None = None
    template_version: str | None = None
    model: str | None = None


class CheckResult(Model):
    id: UUID
    case_id: UUID
    evaluated_at: AwareDatetime
    input_signature: InputSignature
    findings: list[Finding]
    stale: bool


class RouteLeg(Model):
    departure_country: CountryCode | None = None
    arrival_country: CountryCode | None = None
    departure_airport: str | None = None
    arrival_airport: str | None = None
    departure_at: AwareDatetime | None = None
    arrival_at: AwareDatetime | None = None


class Transfer(Model):
    country: CountryCode
    airport: str | None = None
    arrival_at: AwareDatetime | None = None
    departure_at: AwareDatetime | None = None
    ticket_type: Literal["single", "separate"] | None = None
    leaves_airside: StrictBool | None = None
    rechecks_baggage: StrictBool | None = None
    permissions: list[ExistingPermission] | None = None


class Route(Model):
    legs: list[RouteLeg]
    transfers: list[Transfer]


class RouteResult(Model):
    status: Literal["no_known_blockers", "blocked", "needs_verification"]
    findings: list[Finding]
    covered_countries: list[CountryCode]
    uncovered_countries: list[CountryCode]
    flight_availability: Literal["not_checked"] = "not_checked"


class LetterCreate(Model):
    type: Literal["cover_letter"] = "cover_letter"


class LetterPatch(Model):
    content: str = PydanticField(min_length=1, max_length=32000)


class Letter(Model):
    id: UUID
    case_id: UUID
    type: Literal["cover_letter"] = "cover_letter"
    language: Literal["en"] = "en"
    content: str
    missing_facts: list[str]
    generation_mode: Literal["ai", "template"]
    from_cache: bool
    input_signature: InputSignature
    stale: bool
    edited_by_user: bool
    created_at: AwareDatetime


class EventCreate(Model):
    event_type: EventType
    occurred_at: AwareDatetime
    note: Annotated[str, PydanticField(max_length=4000)] | None = None
    outcome: Outcome | None = None


class Event(EventCreate):
    id: UUID
    case_id: UUID
    received_at: AwareDatetime
    source: Literal["manual"] = "manual"


class Notification(Model):
    code: str
    message: str
    next_action: str
    event_id: UUID | None = None


class UserContext(Model):
    """Internal only: produced by server auth, never parsed from a client body."""

    user_id: UUID


class ConfirmedDocumentFacts(Model):
    document_id: UUID
    document_type: DocumentType
    facts_revision: Revision
    fields: list[Field]


class CaseSnapshot(Model):
    owner: UserContext
    case: Case
    profile: ApplicantProfile
    scenario: Scenario
    ruleset: Ruleset
    documents: list[ConfirmedDocumentFacts]
    input_signature: InputSignature


class LetterDraft(Model):
    content: str
    missing_facts: list[str]
    generation_mode: Literal["ai", "template"]
    from_cache: bool


class AIResult(Model):
    status: Literal["available", "unavailable"]
    output: JsonValue = None
    reason: str | None = None
    model: str | None = None
    from_cache: bool = False
