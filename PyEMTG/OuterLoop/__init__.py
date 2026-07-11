"""Deterministic autonomous outer-loop optimization for EMTG."""

from .config import CampaignConfig, ConfigError
from .genome import GenomeSchema, decode_genotype, random_genotype
from .model import (
    CandidateRecord,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    ScoredEvaluationResult,
    Genotype,
    MissionPhenotype,
    ArtifactRef,
    ComparisonContext,
)
from .nsga2 import NSGA2Engine, NSGA2Individual
from .campaign import Campaign
from .evaluator import Evaluator
from .objectives import ConstraintRegistry, ObjectiveRegistry
from .operators import OperatorRegistry
from .seeds import (
    ExactTransferConverter,
    SameShapeBodySubstitutionConverter,
    SinglePhaseJourneyConverter,
    TPSLTToPSFBConverter,
    SeedConverter,
    SeedConverterRegistry,
    SeedProvider,
)
from .workers import WorkerBackend, FakeQueueBackend, QueueRequest, QueueResult, QueueTransport

__all__ = [
    "CampaignConfig",
    "Campaign",
    "CandidateRecord",
    "ConfigError",
    "EvaluationRequest",
    "EvaluationResult",
    "ScoredEvaluationResult",
    "Evaluator",
    "EvaluationStatus",
    "GenomeSchema",
    "Genotype",
    "MissionPhenotype",
    "ArtifactRef",
    "ComparisonContext",
    "NSGA2Engine",
    "NSGA2Individual",
    "ObjectiveRegistry",
    "ConstraintRegistry",
    "OperatorRegistry",
    "SeedProvider",
    "SeedConverter",
    "SeedConverterRegistry",
    "ExactTransferConverter",
    "SameShapeBodySubstitutionConverter",
    "SinglePhaseJourneyConverter",
    "TPSLTToPSFBConverter",
    "WorkerBackend",
    "QueueTransport",
    "QueueRequest",
    "QueueResult",
    "FakeQueueBackend",
    "decode_genotype",
    "random_genotype",
]

__version__ = "0.3.0"
