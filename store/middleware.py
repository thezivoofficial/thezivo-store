from .customer_auth import get_customer


class CustomerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.customer = get_customer(request)
        return self.get_response(request)
