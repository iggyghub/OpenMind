from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Dict, List

logger = logging.getLogger(__name__)

@dataclass
class StructuredAlert:
    severity: str  # "critical", "warning", "info"
    event_type: str
    message: str
    context: Dict = field(default_factory=dict)

class AlertDispatcher:
    """Collects and forwards structured alerts for the S6 alerting system."""
    def __init__(self) -> None:
        self._listeners: List[Callable[[StructuredAlert], None]] = []
        self._history: List[StructuredAlert] = []

    def register_listener(self, fn: Callable[[StructuredAlert], None]) -> None:
        self._listeners.append(fn)

    def emit(self, alert: StructuredAlert) -> None:
        self._history.append(alert)
        for fn in self._listeners:
            try:
                fn(alert)
            except Exception as e:
                logger.error(f"[alerts] Listener failed: {e}")
        
        log_level = logging.WARNING if alert.severity == "warning" else logging.INFO
        if alert.severity == "critical":
            log_level = logging.CRITICAL
            
        logger.log(log_level, f"[{alert.severity.upper()}] {alert.event_type}: {alert.message}")

    def get_pending(self) -> List[StructuredAlert]:
        return list(self._history)
