"""Safe universe catalogs, topology checks, and point-group mission rules."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from .model import MissionPhenotype, RepairRecord, RepairStatus


class UniverseError(ValueError):
    pass


@dataclass(frozen=True)
class BodyInfo:
    name: str
    short_name: str
    universe_number: int
    spice_id: int
    minimum_flyby_altitude: float
    mu: float
    radius: float
    semimajor_axis: float
    eccentricity: float
    inclination_degrees: float
    raan_degrees: float
    argument_of_periapsis_degrees: float
    mean_anomaly_degrees: float

    @property
    def flyby_enabled(self) -> bool:
        return self.minimum_flyby_altitude > 0.0


@dataclass(frozen=True)
class UniverseCatalog:
    path: Path
    central_body: str
    central_spice_id: int
    central_radius: float
    central_mu: float
    bodies: Mapping[str, BodyInfo]
    flyby_menu: tuple[str, ...]

    @classmethod
    def from_file(cls, path: str | Path) -> "UniverseCatalog":
        source = Path(path).resolve()
        central_body = ""
        central_spice_id = 0
        central_radius = 0.0
        central_mu = 0.0
        bodies: dict[str, BodyInfo] = {}
        in_body_list = False
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise UniverseError(f"cannot read universe file {source}: {error}") from error
        for line_number, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line == "begin_body_list":
                in_body_list = True
                continue
            if line == "end_body_list":
                in_body_list = False
                continue
            fields = line.split()
            if not in_body_list:
                key = fields[0]
                try:
                    if key == "central_body_name":
                        central_body = fields[1]
                    elif key == "central_body_SPICE_ID":
                        central_spice_id = int(float(fields[1]))
                    elif key == "central_body_radius":
                        central_radius = float(fields[1])
                    elif key == "mu":
                        central_mu = float(fields[1])
                except (IndexError, ValueError) as error:
                    raise UniverseError(f"invalid universe header at line {line_number}") from error
                continue
            if len(fields) < 13:
                raise UniverseError(f"invalid body row at line {line_number}")
            try:
                orbital = [float(value) for value in fields[-6:]]
                body = BodyInfo(
                    name=fields[0],
                    short_name=fields[1],
                    universe_number=int(float(fields[2])),
                    spice_id=int(float(fields[3])),
                    minimum_flyby_altitude=float(fields[4]),
                    mu=float(fields[5]),
                    radius=float(fields[6]),
                    semimajor_axis=orbital[0],
                    eccentricity=orbital[1],
                    inclination_degrees=orbital[2],
                    raan_degrees=orbital[3],
                    argument_of_periapsis_degrees=orbital[4],
                    mean_anomaly_degrees=orbital[5],
                )
            except ValueError as error:
                raise UniverseError(f"invalid numeric body field at line {line_number}") from error
            if body.name in bodies:
                raise UniverseError(f"duplicate body {body.name}")
            bodies[body.name] = body
        if not central_body or central_mu <= 0 or central_radius <= 0 or not bodies:
            raise UniverseError("universe is missing central-body or body-list data")
        for body in bodies.values():
            if body.mu <= 0 or body.radius <= 0 or body.semimajor_axis < 0:
                raise UniverseError(f"body {body.name} has invalid mu, radius, or semimajor axis")
            if body.semimajor_axis > 0 and not 0.0 <= body.eccentricity < 1.0:
                raise UniverseError(f"body {body.name} has invalid eccentricity")
            if body.minimum_flyby_altitude < -1.0:
                raise UniverseError(f"body {body.name} has invalid minimum flyby altitude")
        menu = tuple(body.name for body in bodies.values() if body.flyby_enabled)
        return cls(source, central_body, central_spice_id, central_radius, central_mu, bodies, menu)

    def body(self, name: str) -> BodyInfo:
        try:
            return self.bodies[name]
        except KeyError as error:
            raise UniverseError(f"body {name!r} is not in {self.path.name}") from error

    def body_index(self, name: str) -> int:
        return self.body(name).universe_number

    def flyby_index(self, name: str) -> int:
        try:
            return self.flyby_menu.index(name) + 1
        except ValueError as error:
            raise UniverseError(f"body {name!r} is not on the flyby menu") from error


@dataclass(frozen=True)
class PointGroup:
    name: str
    members: frozenset[str]
    minimum_visits: int = 0
    maximum_visits: int | None = None
    score_per_member: float = 0.0
    completion_bonus: float = 0.0
    members_to_score: int | None = None
    distinct_members: bool = True
    score_cap: float | None = None
    target_role: str = "optional"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PointGroup":
        allowed = {
            "name", "members", "minimum_visits", "maximum_visits", "score_per_member",
            "completion_bonus", "members_to_score", "distinct_members",
            "score_cap", "target_role",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown point-group fields: {', '.join(sorted(unknown))}")
        members = frozenset(str(value) for value in data.get("members", ()))
        if not members:
            raise ValueError("point group requires members")
        result = cls(
            name=str(data["name"]),
            members=members,
            minimum_visits=int(data.get("minimum_visits", 0)),
            maximum_visits=int(data["maximum_visits"]) if data.get("maximum_visits") is not None else None,
            score_per_member=float(data.get("score_per_member", 0.0)),
            completion_bonus=float(data.get("completion_bonus", 0.0)),
            members_to_score=int(data["members_to_score"]) if data.get("members_to_score") is not None else None,
            distinct_members=bool(data.get("distinct_members", True)),
            score_cap=float(data["score_cap"]) if data.get("score_cap") is not None else None,
            target_role=str(data.get("target_role", "optional")),
        )
        if result.minimum_visits < 0:
            raise ValueError("point-group minimum_visits cannot be negative")
        if result.maximum_visits is not None and result.maximum_visits < result.minimum_visits:
            raise ValueError("point-group maximum_visits is below minimum_visits")
        if result.members_to_score is not None and result.members_to_score < 0:
            raise ValueError("point-group members_to_score cannot be negative")
        if not math.isfinite(result.score_per_member) or not math.isfinite(result.completion_bonus):
            raise ValueError("point-group scores must be finite")
        if result.score_cap is not None and (not math.isfinite(result.score_cap) or result.score_cap < 0):
            raise ValueError("point-group score_cap must be finite and nonnegative")
        if result.target_role not in {"mandatory", "optional"}:
            raise ValueError("point-group target_role must be mandatory or optional")
        return result

    def evaluate(self, visits: Iterable[str]) -> dict[str, Any]:
        selected = [value for value in visits if value in self.members]
        count = len(set(selected)) if self.distinct_members else len(selected)
        scored_count = min(count, self.members_to_score) if self.members_to_score is not None else count
        complete = count >= self.minimum_visits
        violation = max(0, self.minimum_visits - count)
        if self.maximum_visits is not None:
            violation += max(0, count - self.maximum_visits)
        score = scored_count * self.score_per_member + (self.completion_bonus if complete else 0.0)
        if self.score_cap is not None:
            score = min(score, self.score_cap)
        return {
            "name": self.name, "visits": count, "score": score, "complete": complete,
            "violation": violation, "target_role": self.target_role,
        }


@dataclass(frozen=True)
class MissionRules:
    mandatory_destinations: frozenset[str] = frozenset()
    forbidden_pairs: frozenset[tuple[str, str]] = frozenset()
    forbidden_successive: frozenset[tuple[str, str]] = frozenset()
    allowed_successive: frozenset[tuple[str, str]] = frozenset()
    maximum_repeats: Mapping[str, int] = field(default_factory=dict)
    point_groups: tuple[PointGroup, ...] = ()


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    group_results: tuple[Mapping[str, Any], ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues


def validate_phenotype(
    phenotype: MissionPhenotype,
    catalog: UniverseCatalog,
    rules: MissionRules = MissionRules(),
) -> ValidationReport:
    issues: list[ValidationIssue] = []
    visits: list[str] = []
    previous_arrival: str | None = None
    for journey_index, journey in enumerate(phenotype.journeys):
        sequence = journey.sequence
        visits.extend(sequence[1:])
        for body in sequence:
            if body not in catalog.bodies and body != catalog.central_body:
                issues.append(ValidationIssue("unknown_body", f"unknown body {body}", f"journeys[{journey_index}]"))
        for flyby_index, body in enumerate(journey.flybys):
            if body in catalog.bodies and not catalog.body(body).flyby_enabled:
                issues.append(ValidationIssue("illegal_flyby", f"{body} is not on the flyby menu", f"journeys[{journey_index}].flybys[{flyby_index}]"))
        if previous_arrival is not None and journey.departure != previous_arrival:
            issues.append(ValidationIssue("disconnected_journeys", "journey departure does not match prior arrival", f"journeys[{journey_index}].departure"))
        if len(journey.phases) != len(journey.flybys) + 1:
            issues.append(ValidationIssue("phase_count", "phase count does not match sequence", f"journeys[{journey_index}].phases"))
        for left, right in zip(sequence, sequence[1:]):
            pair = (left, right)
            unordered = tuple(sorted(pair))
            if pair in rules.forbidden_successive or unordered in rules.forbidden_pairs:
                issues.append(ValidationIssue("forbidden_pair", f"forbidden succession {left}->{right}", f"journeys[{journey_index}]"))
            if rules.allowed_successive and pair not in rules.allowed_successive:
                issues.append(ValidationIssue("successive_not_allowed", f"succession {left}->{right} is not allowed", f"journeys[{journey_index}]"))
        previous_arrival = journey.arrival
    missing = rules.mandatory_destinations - set(visits)
    for body in sorted(missing):
        issues.append(ValidationIssue("mandatory_destination", f"mandatory destination {body} was not visited", "mission"))
    for body, limit in rules.maximum_repeats.items():
        if visits.count(body) > limit:
            issues.append(ValidationIssue("maximum_repeats", f"{body} exceeds repeat limit {limit}", "mission"))
    groups = tuple(group.evaluate(visits) for group in rules.point_groups)
    for result in groups:
        if result["violation"]:
            issues.append(ValidationIssue("point_group", f"group {result['name']} has violation {result['violation']}", "mission"))
    return ValidationReport(tuple(issues), groups)


def inclination_separation(left: BodyInfo, right: BodyInfo) -> float:
    left_i, right_i = math.radians(left.inclination_degrees), math.radians(right.inclination_degrees)
    left_node, right_node = math.radians(left.raan_degrees), math.radians(right.raan_degrees)
    cosine = math.cos(left_i) * math.cos(right_i) + math.sin(left_i) * math.sin(right_i) * math.cos(left_node - right_node)
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def attach_point_group_metadata(phenotype: MissionPhenotype, groups: Iterable[PointGroup]) -> MissionPhenotype:
    visits = [body for journey in phenotype.journeys for body in journey.sequence[1:]]
    results = {group.name: group.evaluate(visits) for group in groups}
    return replace(phenotype, point_group=results)


def repair_point_groups(
    phenotype: MissionPhenotype, groups: Iterable[PointGroup]
) -> MissionPhenotype:
    """Explicitly replace existing flybys to satisfy minimum group visits.

    The repair never inserts a phase or changes an endpoint.  If existing
    flyby slots cannot satisfy the configured minimum, the candidate is
    rejected instead of being silently expanded.
    """
    groups = tuple(groups)
    journeys = list(phenotype.journeys)
    repairs = list(phenotype.repairs)
    protected = set().union(*(group.members for group in groups if group.minimum_visits > 0))
    for group in sorted(groups, key=lambda value: value.name):
        visits = [body for journey in journeys for body in journey.sequence[1:]]
        result = group.evaluate(visits)
        needed = max(0, group.minimum_visits - int(result["visits"]))
        if not needed:
            continue
        missing_members = [body for body in sorted(group.members) if body not in visits]
        replaceable = [
            (journey_index, flyby_index)
            for journey_index, journey in enumerate(journeys)
            for flyby_index, body in enumerate(journey.flybys)
            if body not in protected or body in group.members
        ]
        if len(replaceable) < needed or len(missing_members) < needed:
            raise ValueError(f"point group {group.name} cannot be repaired without inserting phases")
        for body, (journey_index, flyby_index) in zip(missing_members[:needed], replaceable[:needed]):
            journey = journeys[journey_index]
            flybys = list(journey.flybys)
            before = flybys[flyby_index]
            flybys[flyby_index] = body
            phases = list(journey.phases)
            phases[flyby_index] = replace(phases[flyby_index], target=body)
            journeys[journey_index] = replace(journey, flybys=tuple(flybys), phases=tuple(phases))
            repairs.append(
                RepairRecord(
                    f"journeys[{journey_index}].flybys[{flyby_index}]",
                    before,
                    body,
                    f"satisfy point group {group.name}",
                )
            )
    repaired = replace(
        phenotype,
        journeys=tuple(journeys),
        repair_status=RepairStatus.REPAIRED if repairs else phenotype.repair_status,
        repairs=tuple(repairs),
    )
    return attach_point_group_metadata(repaired, groups)
