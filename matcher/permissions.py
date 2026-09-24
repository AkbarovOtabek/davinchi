from django.conf import settings
from rest_framework.permissions import BasePermission


class OptionalTokenPermission(BasePermission):
    """Если в .env задан API_TOKEN — требуем заголовок X-API-Token."""

    message = "Неверный или отсутствующий X-API-Token."

    def has_permission(self, request, view) -> bool:
        token = getattr(settings, "API_TOKEN", "")
        if not token:
            return True
        return request.headers.get("X-API-Token") == token
