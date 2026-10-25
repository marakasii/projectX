"""Compose package 2's registry and transit files without changing its contract."""

from importlib.resources import files
from pathlib import Path

from app.common.errors import AppError
from app.common.models import Ruleset
from app.rules.registry import CatalogManifest, FileRuleRegistry, read_json, source_quality
from .schema import TransitDefinition, VersionManifest, validate_ruleset


class TransitRuleRegistry:
    def __init__(self, destinations=None, root=None):
        self.destinations_registry = destinations if destinations is not None else FileRuleRegistry()
        root = root if root is not None else files("data.rules.transit")
        manifest = CatalogManifest.model_validate(read_json(root.joinpath("manifest.json")))
        if (manifest.active_version not in manifest.versions
            or len(set(manifest.versions)) != len(manifest.versions)):
            raise ValueError("Invalid transit manifest")
        self.transit_version = "transit:" + manifest.active_version
        self._rulesets = {}
        for version in manifest.versions:
            if not version or Path(version).name != version or version in (".", ".."):
                raise ValueError("Invalid transit version directory")
            folder = root.joinpath(version)
            entry = VersionManifest.model_validate(read_json(folder.joinpath("manifest.json")))
            if entry.version != version or set(entry.countries) != {"TR.json", "AE.json"}:
                raise ValueError("Transit catalog must contain TR and AE")
            rules, sources = [], []
            for filename in entry.countries:
                definition = TransitDefinition.model_validate(read_json(folder.joinpath(filename)))
                if definition.country + ".json" != filename or definition.ruleset.version != "transit:" + version:
                    raise ValueError("Transit version/country mismatch")
                rules.extend(definition.ruleset.rules)
                sources.extend(definition.ruleset.sources)
            ruleset = validate_ruleset(Ruleset(version="transit:" + version, rules=rules, sources=sources))
            self._rulesets[ruleset.version] = ruleset

    @property
    def active_version(self):
        return self.destinations_registry.active_version

    def get_ruleset(self, version, *, as_of=None):
        if not version.startswith("transit:"):
            return self.destinations_registry.get_ruleset(version, as_of=as_of)
        if version not in self._rulesets:
            raise AppError(404, "ruleset_not_found", "Версия транзитных правил не найдена.")
        result = self._rulesets[version].model_copy(deep=True)
        for source in result.sources:
            source.status = source_quality(source, as_of)
        return result

    def get_scenario(self, scenario_id, *, version=None, as_of=None):
        return self.destinations_registry.get_scenario(scenario_id, version=version, as_of=as_of)

    def get_sources(self, source_ids, *, version=None):
        if version is None or not version.startswith("transit:"):
            return self.destinations_registry.get_sources(source_ids, version=version)
        sources = {source.id: source for source in self.get_ruleset(version).sources}
        if not set(source_ids) <= sources.keys():
            raise AppError(404, "source_not_found", "Источник не найден.")
        return [sources[source_id] for source_id in source_ids]

    def destinations(self, *, as_of=None):
        return self.destinations_registry.destinations(as_of=as_of)
