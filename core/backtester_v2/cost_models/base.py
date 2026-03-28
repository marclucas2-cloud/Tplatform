"""Abstract cost model and factory for broker-specific commissions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class CostModel(ABC):
    """Abstract base for broker commission models."""

    @abstractmethod
    def calculate_commission(self, order: Any, fill_price: float) -> float:
        """Calculate commission for a filled order.

        Args:
            order: The Order being filled.
            fill_price: Actual fill price after slippage.

        Returns:
            Commission in USD (or base currency).
        """
        ...


class CostModelFactory:
    """Factory to create cost models from config."""

    _registry: Dict[str, type] = {}

    @classmethod
    def register(cls, name: str, model_class: type) -> None:
        """Register a cost model class under a name."""
        cls._registry[name] = model_class

    @classmethod
    def create(cls, brokers_config: Dict[str, Dict[str, Any]]) -> Dict[str, CostModel]:
        """Create cost model instances from config dict.

        Args:
            brokers_config: Mapping of broker_name -> config kwargs.
                Example: {"ibkr": {}, "binance": {"bnb_discount": True}}

        Returns:
            Mapping of broker_name -> CostModel instance.
        """
        # Late import to avoid circular deps
        from core.backtester_v2.cost_models.ibkr_costs import IBKRCostModel
        from core.backtester_v2.cost_models.binance_costs import BinanceCostModel

        cls._registry.setdefault("ibkr", IBKRCostModel)
        cls._registry.setdefault("binance", BinanceCostModel)

        models: Dict[str, CostModel] = {}
        for broker_name, cfg in brokers_config.items():
            model_cls = cls._registry.get(broker_name)
            if model_cls is None:
                raise ValueError(f"Unknown broker cost model: {broker_name}")
            models[broker_name] = model_cls(**cfg)
        return models
