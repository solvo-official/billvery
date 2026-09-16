from typing import Any


class AppError(Exception):
    """Base for errors that map to a specific HTTP response."""

    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class InvalidReference(AppError):
    """The request is well-formed but refers to something that does not exist for this tenant."""

    status_code = 422
    code = "invalid_reference"


class InvalidParameter(AppError):
    status_code = 422
    code = "invalid_parameter"


class PayloadTooLarge(AppError):
    status_code = 413
    code = "payload_too_large"


class UnsupportedMediaType(AppError):
    status_code = 415
    code = "unsupported_media_type"


class ServiceUnavailable(AppError):
    status_code = 503
    code = "unavailable"
