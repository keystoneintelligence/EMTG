"""Deterministic local workers and backend extension contract."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
import threading
from typing import Any, Iterator, Mapping, Protocol, Sequence

from .evaluator import Evaluator
from .model import EvaluationRequest, EvaluationResult, EvaluationStatus
from .serde import candidate_from_dict, candidate_to_dict, result_from_dict, result_to_dict


class WorkerBackend(Protocol):
    def evaluate(
        self,
        requests: Sequence[EvaluationRequest],
        evaluator: Evaluator,
        cancel_event: threading.Event | None = None,
    ) -> list[EvaluationResult]: ...


@dataclass(frozen=True)
class RetryPolicy:
    infrastructure_retries: int = 1

    def retryable(self, result: EvaluationResult) -> bool:
        return (
            result.status is EvaluationStatus.INFRASTRUCTURE_FAILED
            and result.provenance.get("transient", True) is True
        )


class LocalWorkerBackend:
    def __init__(self, max_workers: int = 4, retry_policy: RetryPolicy = RetryPolicy()):
        if max_workers < 1:
            raise ValueError("max_workers must be positive")
        self.max_workers = max_workers
        self.retry_policy = retry_policy

    def _evaluate_one(
        self,
        request: EvaluationRequest,
        evaluator: Evaluator,
        cancel_event: threading.Event | None,
    ) -> EvaluationResult:
        attempts: list[dict[str, object]] = []
        result: EvaluationResult | None = None
        for attempt in range(self.retry_policy.infrastructure_retries + 1):
            result = evaluator.evaluate(request, cancel_event)
            attempts.append({"attempt": attempt, "status": result.status.value, "reason": result.failure_reason})
            if not self.retry_policy.retryable(result):
                break
            if cancel_event is not None and cancel_event.is_set():
                break
        assert result is not None
        provenance = dict(result.provenance)
        provenance["worker_attempts"] = attempts
        return replace(result, provenance=provenance)

    def evaluate(
        self,
        requests: Sequence[EvaluationRequest],
        evaluator: Evaluator,
        cancel_event: threading.Event | None = None,
    ) -> list[EvaluationResult]:
        results: list[EvaluationResult | None] = [None] * len(requests)
        for index, result in self.evaluate_stream(requests, evaluator, cancel_event):
            results[index] = result
        return [result for result in results if result is not None]

    def evaluate_stream(
        self,
        requests: Sequence[EvaluationRequest],
        evaluator: Evaluator,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[tuple[int, EvaluationResult]]:
        """Yield completed work for immediate persistence.

        Consumers must restore slot order before evolution.  Persistence order
        is deliberately allowed to follow worker completion order.
        """
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="emtg-outerloop") as executor:
            futures: dict[Future[EvaluationResult], int] = {
                executor.submit(self._evaluate_one, request, evaluator, cancel_event): index
                for index, request in enumerate(requests)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    request = requests[index]
                    result = EvaluationResult(
                        request.evaluation_key,
                        request.candidate.candidate_id,
                        EvaluationStatus.EXECUTION_FAILED,
                        request.fidelity,
                        failure_reason=f"worker exception: {error}",
                    )
                yield index, result


class ExternalQueueBackend:
    """Scheduler-neutral request/result protocol adapter.

    Concrete Slurm/MPI/PEATSA launchers remain extensions; transports only need
    deterministic submit and receive operations.
    """

    def __init__(self, transport: "QueueTransport | None" = None):
        self.transport = transport

    def evaluate(
        self,
        requests: Sequence[EvaluationRequest],
        evaluator: Evaluator,
        cancel_event: threading.Event | None = None,
    ) -> list[EvaluationResult]:
        if self.transport is None:
            raise RuntimeError("an ExternalQueueBackend QueueTransport is required")
        for request in requests:
            self.transport.submit(QueueRequest.from_evaluation_request(request))
        results: dict[str, EvaluationResult] = {}
        while len(results) < len(requests):
            if cancel_event is not None and cancel_event.is_set():
                break
            response = self.transport.receive(timeout_seconds=0.25)
            if response is not None:
                results[response.evaluation_key] = response.result
        return [results[request.evaluation_key] for request in requests if request.evaluation_key in results]


@dataclass(frozen=True)
class QueueRequest:
    protocol_version: int
    evaluation_key: str
    request: EvaluationRequest

    @classmethod
    def from_evaluation_request(cls, request: EvaluationRequest) -> "QueueRequest":
        return cls(3, request.evaluation_key, request)

    def to_dict(self) -> dict[str, Any]:
        request = self.request
        return {
            "protocol_version": 3,
            "evaluation_key": self.evaluation_key,
            "request": {
                "candidate": candidate_to_dict(request.candidate),
                "fidelity": request.fidelity,
                "evaluation_seed": request.evaluation_seed,
                "budget": dict(request.budget),
                "initial_guess": request.initial_guess,
                "context": dict(request.context),
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueueRequest":
        if data.get("protocol_version") != 3 or set(data) != {"protocol_version", "evaluation_key", "request"}:
            raise ValueError("unsupported or malformed queue request")
        raw = data["request"]
        if not isinstance(raw, Mapping) or set(raw) != {
            "candidate", "fidelity", "evaluation_seed", "budget", "initial_guess", "context"
        }:
            raise ValueError("malformed queue evaluation request")
        request = EvaluationRequest(
            candidate_from_dict(raw["candidate"]),
            str(raw["fidelity"]),
            int(raw["evaluation_seed"]),
            dict(raw["budget"]),
            raw["initial_guess"],
            dict(raw["context"]),
        )
        if request.evaluation_key != data["evaluation_key"]:
            raise ValueError("queue request evaluation key does not match its content")
        return cls(3, request.evaluation_key, request)


@dataclass(frozen=True)
class QueueResult:
    protocol_version: int
    evaluation_key: str
    result: EvaluationResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": 3,
            "evaluation_key": self.evaluation_key,
            "result": result_to_dict(self.result),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueueResult":
        if data.get("protocol_version") != 3 or set(data) != {"protocol_version", "evaluation_key", "result"}:
            raise ValueError("unsupported or malformed queue result")
        result = result_from_dict(data["result"])
        if result.evaluation_key != data["evaluation_key"]:
            raise ValueError("queue result evaluation key does not match its content")
        return cls(3, result.evaluation_key, result)


class QueueTransport(Protocol):
    def submit(self, request: QueueRequest) -> None: ...
    def receive(self, timeout_seconds: float) -> QueueResult | None: ...


class FakeQueueBackend:
    """In-memory backend used to contract-test the distributed protocol."""

    def evaluate(
        self,
        requests: Sequence[EvaluationRequest],
        evaluator: Evaluator,
        cancel_event: threading.Event | None = None,
    ) -> list[EvaluationResult]:
        envelopes = [
            QueueRequest.from_dict(QueueRequest.from_evaluation_request(request).to_dict())
            for request in requests
        ]
        responses = []
        for envelope in reversed(envelopes):
            if envelope.protocol_version != 3:
                raise ValueError("unsupported queue protocol")
            if cancel_event is not None and cancel_event.is_set():
                break
            result = evaluator.evaluate(envelope.request, cancel_event)
            response = QueueResult(3, envelope.evaluation_key, result)
            responses.append(QueueResult.from_dict(response.to_dict()))
        by_key = {response.evaluation_key: response.result for response in responses}
        return [by_key[request.evaluation_key] for request in requests if request.evaluation_key in by_key]
