from __future__ import annotations

from abc import ABC, abstractmethod

from backend.normalize.schemas import NormalizationPolicy, NormalizationResult


class NormalizerStrategy(ABC):
    method_name: str

    @abstractmethod
    def normalize(
        self,
        ticker: str,
        policy: NormalizationPolicy,
        start_year: int | None = None,
        end_year: int | None = None,
    ) -> NormalizationResult:
        """Return a normalization result or an empty series if the strategy cannot handle it."""

