from functools import wraps
from django.shortcuts import redirect


def get_customer(request):
    cid = request.session.get("customer_id")
    if not cid:
        return None
    from .models import Customer
    return Customer.objects.filter(pk=cid, is_active=True).first()


def merge_session_to_db(request, customer):
    """Merge guest session cart/wishlist into DB on login."""
    from .models import SKU, Product, CartItem, WishlistItem

    for sku_id_str, qty in request.session.get("cart", {}).items():
        try:
            sku = SKU.objects.get(id=int(sku_id_str))
            item, created = CartItem.objects.get_or_create(
                customer=customer, sku=sku, defaults={"quantity": 0})
            item.quantity += int(qty)
            item.save()
        except (ValueError, SKU.DoesNotExist):
            pass

    for pid_str in request.session.get("wishlist", []):
        try:
            product = Product.objects.get(id=int(pid_str))
            WishlistItem.objects.get_or_create(customer=customer, product=product)
        except (ValueError, Product.DoesNotExist):
            pass

    request.session.pop("cart", None)
    request.session.pop("wishlist", None)


def customer_login(request, customer):
    cart     = request.session.get("cart", {})
    wishlist = request.session.get("wishlist", [])
    request.session.cycle_key()
    request.session["customer_id"] = customer.pk
    # Temporarily restore so merge can read them
    request.session["cart"]     = cart
    request.session["wishlist"] = wishlist
    merge_session_to_db(request, customer)


def customer_logout(request):
    request.session.pop("customer_id", None)


def customer_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not getattr(request, "customer", None):
            return redirect("login")
        return view_func(request, *args, **kwargs)
    return wrapper
