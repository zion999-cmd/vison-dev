"""
Event Bus - Simple pub/sub event bus for runtime communication.
"""

import logging
from typing import Callable, Dict, List, Any
from collections import defaultdict

logger = logging.getLogger("EventBus")


class EventBus:
    """Simple in-process event bus."""

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callable):
        """Subscribe to an event type."""
        self._subscribers[event_type].append(callback)
        logger.debug("Subscribed to '%s'", event_type)

    def unsubscribe(self, event_type: str, callback: Callable):
        """Unsubscribe from an event type."""
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)

    def emit(self, event_type: str, data: Any = None):
        """Emit an event to all subscribers."""
        for cb in self._subscribers.get(event_type, []):
            try:
                cb(data)
            except Exception as e:
                logger.error("Event handler error for '%s': %s", event_type, e)

    def clear(self):
        self._subscribers.clear()
