"""Configuration-driven validation for external data deliveries."""

from .validation import IntakeValidationError, validate_delivery

__all__ = ["IntakeValidationError", "validate_delivery"]

