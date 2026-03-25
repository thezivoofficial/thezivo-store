from django.conf import settings
from .customer_auth import get_customer


class CustomerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.customer = get_customer(request)
        return self.get_response(request)


class PermissionsPolicyMiddleware:
    """Attach Permissions-Policy header to every response."""
    def __init__(self, get_response):
        self.get_response = get_response
        self.policy = getattr(settings, "PERMISSIONS_POLICY", "")

    def __call__(self, request):
        response = self.get_response(request)
        if self.policy:
            response["Permissions-Policy"] = self.policy
        return response
