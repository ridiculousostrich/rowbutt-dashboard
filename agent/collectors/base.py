"""Abstract collector interface and registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any


@dataclass
class CollectResult:
    """Standardised result from a single collector poll."""
    timestamp: str            # ISO-8601
    collector_name: str
    success: bool
    data: Dict[str, Any]      # collector-specific fields
    error: Optional[str] = None


class Collector(ABC):
    """Base class for all data collectors.

    Subclasses must implement ``collect()`` and set ``name``.
    """

    name: str = "unnamed"

    @abstractmethod
    def collect(self) -> CollectResult:
        """Perform one collection cycle and return the result."""
        ...

    @abstractmethod
    def validate_config(self) -> List[str]:
        """Return a list of configuration errors (empty = valid)."""
        ...


# ── Registry ────────────────────────────────────────────────

_registry: Dict[str, type] = {}


def register(cls: type) -> type:
    """Decorator: register a collector class by its name."""
    name = getattr(cls, "name", None)
    if not name:
        raise ValueError(f"Collector {cls.__name__} must define a class-level 'name'")
    _registry[name] = cls
    return cls


def get_collector(name: str) -> type:
    """Look up a collector class by name."""
    if name not in _registry:
        raise KeyError(f"Unknown collector: {name}. Available: {list(_registry.keys())}")
    return _registry[name]


def list_collectors() -> List[str]:
    """Return names of all registered collectors."""
    return list(_registry.keys())
