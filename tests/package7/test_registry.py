import json
import shutil
from pathlib import Path

import pytest

from app.common.errors import AppError
from app.itinerary.registry import TransitRuleRegistry
from app.rules.registry import FileRuleRegistry
from .conftest import NOW


def test_composition_preserves_all_twelve_destination_contracts(registry):
    original = FileRuleRegistry()
    assert registry.active_version == original.active_version
    assert registry.destinations(as_of=NOW.date()) == original.destinations(as_of=NOW.date())
    assert registry.get_ruleset(original.active_version, as_of=NOW.date()) == original.get_ruleset(original.active_version, as_of=NOW.date())
    assert registry.get_scenario("FR_TOURISM", as_of=NOW.date()) == original.get_scenario("FR_TOURISM", as_of=NOW.date())
    transit = registry.get_ruleset(registry.transit_version, as_of=NOW.date())
    assert len(transit.rules) == 5 and len(transit.sources) == 5
    assert all(source.status == "source_checked" and source.evidence and source.locator for source in transit.sources)
    transit.rules.clear()
    assert len(registry.get_ruleset(registry.transit_version).rules) == 5
    assert registry.get_sources(["AE_MOFA_MOSCOW"], version=registry.transit_version)[0].authority
    with pytest.raises(AppError):
        registry.get_sources(["missing"], version=registry.transit_version)
    with pytest.raises(AppError):
        registry.get_ruleset("transit:missing")


@pytest.mark.parametrize("mutation", ["path", "version", "files", "kind", "parameter", "source", "duplicate_rule", "condition", "duplicate_key"])
def test_corrupt_or_unbounded_catalog_rejected_before_runtime(tmp_path, mutation):
    root = tmp_path / "transit"
    shutil.copytree(Path("data/rules/transit"), root, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = root / "manifest.json"
    entry = root / "2026-10-25.1/manifest.json"
    path = root / "2026-10-25.1/TR.json"
    body = json.loads(path.read_text())
    if mutation == "path":
        manifest.write_text('{"schema_version":1,"active_version":"../bad","versions":["../bad"]}')
    elif mutation == "files":
        entry.write_text('{"version":"2026-10-25.1","countries":["TR.json","TR.json"]}')
    elif mutation == "duplicate_key":
        path.write_text('{"country":"TR","country":"AE"}')
    else:
        rule = body["ruleset"]["rules"][0]
        if mutation == "version":
            rule["version"] = "wrong"
        elif mutation == "kind":
            rule["kind"] = "execute_python"
        elif mutation == "parameter":
            rule["parameters"]["unbounded"] = "fake legal exemption"
        elif mutation == "source":
            rule["source_ids"] = ["missing"]
        elif mutation == "condition":
            rule["applies_if"] = {"field": "transfer.leaves_airside", "op": "eq", "value": False}
        else:
            body["ruleset"]["rules"].append(rule)
        path.write_text(json.dumps(body))
    with pytest.raises(ValueError):
        TransitRuleRegistry(root=root)
