"""
API Exception Classes
"""
class APIException(Exception):
    """Base API exception"""
    
    def __init__(self, message: str, status_code: int = 400, error_code: str = None):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        super().__init__(self.message)

class ValidationError(APIException):
    """Validation error (400)"""
    
    def __init__(self, message: str = "Validation failed", details: dict = None):
        self.details = details
        super().__init__(message, 400, "VALIDATION_ERROR")

class UnauthorizedError(APIException):
    """Unauthorized error (401)"""
    
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, 401, "UNAUTHORIZED")

class ForbiddenError(APIException):
    """Forbidden error (403)"""
    
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, 403, "FORBIDDEN")

class NotFoundError(APIException):
    """Not found error (404)"""
    
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, 404, "NOT_FOUND")

class ConflictError(APIException):
    """Conflict error (409)"""
    
    def __init__(self, message: str = "Resource conflict"):
        super().__init__(message, 409, "CONFLICT")

class RateLimitError(APIException):
    """Rate limit error (429)"""
    
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(message, 429, "RATE_LIMIT_EXCEEDED")

class InternalServerError(APIException):
    """Internal server error (500)"""
    
    def __init__(self, message: str = "Internal server error"):
        super().__init__(message, 500, "INTERNAL_ERROR")