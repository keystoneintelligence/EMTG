from __future__ import annotations

from pathlib import Path

from OuterLoop.model import JourneyPhenotype, MissionPhenotype, PhasePhenotype
import OuterLoop.ephemeris as ephemeris_module
from OuterLoop.ephemeris import CoverageInterval, EphemerisCoverage
from OuterLoop.hardware import HardwareCatalog
from OuterLoop.prefilters import FilterPipeline, InclinationBandpassPrefilter, TopologyPrefilter
from OuterLoop.physics import C3EnvelopeScreen, HohmannTimeScreen, hohmann_leg_estimate
from OuterLoop.randomness import random_stream
from OuterLoop.resonance import ResonanceRatio, resonance_metadata, resonance_opportunity
from OuterLoop.rules import MissionRules, PointGroup, UniverseCatalog, repair_point_groups, validate_phenotype


ROOT = Path(__file__).resolve().parents[1]


def mission(*flybys: str) -> MissionPhenotype:
    phases = tuple(PhasePhenotype(target, {}) for target in (*flybys, "Mars"))
    return MissionPhenotype({}, (JourneyPhenotype("Earth", "Mars", tuple(flybys), {}, phases),))


def test_safe_universe_catalog_maps_body_and_flyby_indices():
    catalog = UniverseCatalog.from_file(ROOT / "testatron" / "universe" / "Sun.emtg_universe")
    assert catalog.body_index("Earth") == 3
    assert catalog.flyby_index("Earth") == 3
    assert catalog.body("Vesta").flyby_enabled is False


def test_topology_and_point_group_rules_report_typed_violations():
    catalog = UniverseCatalog.from_file(ROOT / "testatron" / "universe" / "Sun.emtg_universe")
    group = PointGroup("inner", frozenset({"Venus", "Earth"}), minimum_visits=2, score_per_member=3, completion_bonus=5)
    rules = MissionRules(point_groups=(group,), forbidden_successive=frozenset({("Earth", "Earth")}))
    valid = validate_phenotype(mission("Venus", "Earth"), catalog, rules)
    assert valid.valid
    assert valid.group_results[0]["score"] == 11
    invalid = validate_phenotype(mission("Earth"), catalog, rules)
    assert {issue.code for issue in invalid.issues} == {"forbidden_pair", "point_group"}


def test_heuristic_filter_audit_can_override_but_strict_cannot():
    catalog = UniverseCatalog.from_file(ROOT / "testatron" / "universe" / "Sun.emtg_universe")
    pipeline = FilterPipeline(
        [TopologyPrefilter(catalog), InclinationBandpassPrefilter(catalog, 0.0)],
        audit_fraction=1.0,
    )
    result = pipeline.evaluate(mission("Venus"), random_stream(3, "audit"))
    assert result.accepted
    assert any(decision.audited for decision in result.decisions)


def test_generic_resonance_screen_uses_universe_physics():
    catalog = UniverseCatalog.from_file(ROOT / "testatron" / "universe" / "Jupiter.emtg_universe")
    opportunity = resonance_opportunity(catalog, "Europa", ResonanceRatio(2, 1))
    assert opportunity.moon == "Europa"
    assert opportunity.spacecraft_period_seconds == opportunity.moon_period_seconds / 2
    assert opportunity.spacecraft_semimajor_axis_km > 0
    assert opportunity.maximum_turning_degrees >= 0
    repeated = MissionPhenotype(
        {},
        (
            JourneyPhenotype(
                "Io",
                "Ganymede",
                ("Europa", "Europa"),
                {},
                (
                    PhasePhenotype("Europa", {}),
                    PhasePhenotype("Europa", {}),
                    PhasePhenotype("Ganymede", {}),
                ),
            ),
        ),
    )
    metadata = resonance_metadata(repeated, catalog, (ResonanceRatio(2, 1),))
    assert metadata["chains"][0]["moon"] == "Europa"


def test_ephemeris_coverage_catalog_reports_body_intervals(tmp_path, monkeypatch):
    kernel_directory = tmp_path / "ephemeris_files"
    kernel_directory.mkdir()
    (kernel_directory / "fixture.bsp").write_bytes(b"fixture")
    monkeypatch.setattr(
        ephemeris_module,
        "_spiceypy_coverage",
        lambda kernels: {399: [CoverageInterval(59000.0, 61000.0)]},
    )

    coverage = EphemerisCoverage.from_directory(kernel_directory)
    assert coverage.covers(399, 60000.0, 60100.0)
    assert not coverage.covers(123456789, 60000.0, 60100.0)
    assert 123456789 in coverage.missing([399, 123456789], 60000.0, 60100.0)


def test_hardware_catalog_validates_named_choices():
    import MissionOptions

    options = MissionOptions.MissionOptions(
        str(ROOT / "testatron" / "tests" / "transcription_tests" / "MGAnDSMs_EMintercept.emtgopt")
    )
    catalog = HardwareCatalog.from_options(ROOT / "testatron" / "HardwareModels", options)
    catalog.validate_choice("launch_vehicle", "Falcon_9_FT_(RTLS)")
    catalog.validate_choice("power_system", "5kW_basic")
    catalog.validate_choice("electric_propulsion_system", "AEPS_PolyFit_HTandHI")
    try:
        catalog.validate_choice("launch_vehicle", "Nonexistent")
    except ValueError:
        pass
    else:
        raise AssertionError("missing hardware key was accepted")


def test_two_body_screens_are_explicit_conservative_heuristics():
    catalog = UniverseCatalog.from_file(ROOT / "testatron" / "universe" / "Sun.emtg_universe")
    estimate = hohmann_leg_estimate(catalog, "Earth", "Mars")
    assert 200.0 < estimate.transfer_time_days < 300.0
    short = MissionPhenotype(
        {"total_flight_time_bounds": [0.0, 10.0]},
        (JourneyPhenotype("Earth", "Mars", (), {}, (PhasePhenotype("Mars", {}),)),),
    )
    accepted, reason, metrics = HohmannTimeScreen(0.25).screen(short, catalog)
    assert not accepted and reason
    assert metrics["hohmann_transfer_time_days"] == estimate.transfer_time_days
    accepted, reason, metrics = C3EnvelopeScreen(maximum_departure_c3=0.0).screen(short, catalog)
    assert not accepted and reason


def test_explicit_point_group_repair_replaces_but_does_not_insert_phases():
    group = PointGroup("required", frozenset({"Earth"}), minimum_visits=1)
    repaired = repair_point_groups(mission("Venus"), (group,))
    assert repaired.journeys[0].flybys == ("Earth",)
    assert repaired.repairs[0].reason == "satisfy point group required"
    try:
        repair_point_groups(mission(), (group,))
    except ValueError as error:
        assert "without inserting" in str(error)
    else:
        raise AssertionError("group repair inserted a phase silently")


def test_point_group_score_cap_and_mandatory_role_are_explicit():
    group = PointGroup.from_dict({
        "name": "moons",
        "members": ["Io", "Europa"],
        "minimum_visits": 1,
        "score_per_member": 10.0,
        "completion_bonus": 5.0,
        "score_cap": 12.0,
        "target_role": "mandatory",
    })
    result = group.evaluate(["Io", "Europa"])
    assert result["score"] == 12.0
    assert result["target_role"] == "mandatory"
