from django.db import models as db_models
from .models import SKU, CartItem, WishlistItem, Coupon
from .utils import calculate_delivery_and_final


def cart_context(request):
    customer = getattr(request, "customer", None)

    items = []
    subtotal = 0
    total_items = 0

    if customer:
        for cart_item in CartItem.objects.filter(customer=customer).select_related("sku__product"):
            sku = cart_item.sku
            qty = cart_item.quantity
            item_subtotal = sku.selling_price * qty
            subtotal += item_subtotal
            total_items += qty
            items.append({"sku": sku, "quantity": qty, "subtotal": item_subtotal})
    else:
        cart = request.session.get("cart", {})
        cleaned_cart = {}
        for sku_id, qty in cart.items():
            try:
                sku = SKU.objects.get(id=int(sku_id))
                qty = int(qty)
                item_subtotal = sku.selling_price * qty
                subtotal += item_subtotal
                total_items += qty
                items.append({"sku": sku, "quantity": qty, "subtotal": item_subtotal})
                cleaned_cart[str(int(sku_id))] = qty
            except (ValueError, SKU.DoesNotExist):
                continue
        request.session["cart"] = cleaned_cart

    delivery_charge, final_amount, remaining = calculate_delivery_and_final(subtotal)

    return {
        "items": items,
        "cart_count": total_items,
        "cart_subtotal": subtotal,
        "cart_total": final_amount,
        "delivery_charge": delivery_charge,
        "free_delivery_remaining": remaining,
    }


def announcement_banner(request):
    from django.utils import timezone
    today = timezone.now().date()
    coupons = Coupon.objects.filter(
        is_active=True,
        show_in_banner=True,
    ).filter(
        db_models.Q(valid_from__isnull=True) | db_models.Q(valid_from__lte=today)
    ).filter(
        db_models.Q(valid_to__isnull=True) | db_models.Q(valid_to__gte=today)
    )
    return {"banner_coupons": coupons}


def wishlist_count(request):
    customer = getattr(request, "customer", None)

    if customer:
        ids = set(WishlistItem.objects.filter(
            customer=customer).values_list("product_id", flat=True))
        return {"wishlist_count": len(ids), "wishlist_ids": ids}

    wishlist = request.session.get("wishlist", [])
    return {
        "wishlist_count": len(wishlist),
        "wishlist_ids": set(int(x) for x in wishlist if str(x).isdigit()),
    }
