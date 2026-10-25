"""Bounded transit extension; shared Rule/Ruleset/Source remain unchanged."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.common.models import Model, Rule, Ruleset


class Scope(Model):
    country: Literal["TR", "AE"]
    airports: list[Annotated[str, Field(pattern=r"^[A-Z]{3}$")]] = Field(min_length=1)
    citizenship: Literal["RU"] = "RU"
    limitations: list[str] = Field(min_length=1)


class AirsideParameters(Scope):
    pass


class EntryParameters(Scope):
    country: Literal["AE"]
    passport_months: Literal[6]
    stay_days: Literal[90]
    window_days: Literal[180]


class EntryContextParameters(Scope):
    country: Literal["TR"]
    message: str


class OperationsParameters(Scope):
    country: Literal["AE"]
    airports: list[Literal["DXB"]] = Field(min_length=1, max_length=1)
    message: str


PARAMETERS = {
    "transit_airside": AirsideParameters,
    "transit_entry_ru": EntryParameters,
    "transit_entry_context": EntryContextParameters,
    "transit_operations": OperationsParameters,
}


def parse_parameters(rule: Rule):
    model = PARAMETERS.get(rule.kind)
    if model is None:
        raise ValueError("Unknown transit rule kind")
    return model.model_validate(rule.parameters)


def validate_ruleset(ruleset: Ruleset) -> Ruleset:
    sources = {source.id: source for source in ruleset.sources}
    if len(sources) != len(ruleset.sources) or len({rule.id for rule in ruleset.rules}) != len(ruleset.rules):
        raise ValueError("Duplicate transit source/rule")
    scopes = set()
    for rule in ruleset.rules:
        parameters = parse_parameters(rule)
        if (rule.version != ruleset.version or rule.scenario_id != parameters.country + "_TRANSIT"
            or rule.applies_if is not None or rule.requirement_id is not None or not rule.mandatory
            or not rule.source_ids or not set(rule.source_ids) <= sources.keys()
            or len(parameters.airports) != len(set(parameters.airports))):
            raise ValueError("Invalid transit references/scope")
        key = (parameters.country, rule.kind)
        if key in scopes:
            raise ValueError("Overlapping transit rules")
        scopes.add(key)
    if not ruleset.rules:
        raise ValueError("Empty transit ruleset")
    return ruleset


class TransitDefinition(Model):
    country: Literal["TR", "AE"]
    ruleset: Ruleset

    @model_validator(mode="after")
    def valid_definition(self):
        validate_ruleset(self.ruleset)
        if any(parse_parameters(rule).country != self.country for rule in self.ruleset.rules):
            raise ValueError("Mixed transit countries")
        return self


class VersionManifest(Model):
    version: str
    countries: list[str] = Field(min_length=2, max_length=2)
