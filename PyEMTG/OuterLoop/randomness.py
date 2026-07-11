"""Stateless deterministic random-stream derivation."""

from __future__ import annotations

import hashlib
import random
from typing import Any

from .canonical import canonical_json


def derive_seed(root_seed: int | str, *coordinates: Any, bits: int = 128) -> int:
    if bits <= 0 or bits > 256:
        raise ValueError("bits must be between 1 and 256")
    payload = canonical_json({"root": str(root_seed), "coordinates": coordinates})
    value = int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest(), "big")
    return value >> (256 - bits)


def random_stream(root_seed: int | str, *coordinates: Any) -> random.Random:
    return random.Random(derive_seed(root_seed, *coordinates))


def deterministic_id(root_seed: int | str, *coordinates: Any, prefix: str = "ind") -> str:
    digest = hashlib.sha256(
        canonical_json({"root": str(root_seed), "coordinates": coordinates}).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:24]}"
