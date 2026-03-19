from django.db import models as db_models
from .models import SKU, CartItem, WishlistItem, Coupon, Announcement, Category
from .utils import calculate_delivery_and_final, calculate_offer_discounts, _get_active_offers


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

    offer_discount, offer_lines = calculate_offer_discounts(items)
    subtotal_after_offers = max(0, subtotal - offer_discount)
    delivery_charge, final_amount, remaining = calculate_delivery_and_final(subtotal_after_offers)

    return {
        "items": items,
        "cart_count": total_items,
        "cart_subtotal": subtotal,
        "offer_discount": offer_discount,
        "offer_lines": offer_lines,
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
    try:
        announcements = Announcement.objects.filter(
            is_active=True,
        ).filter(
            db_models.Q(valid_from__isnull=True) | db_models.Q(valid_from__lte=today)
        ).filter(
            db_models.Q(valid_to__isnull=True) | db_models.Q(valid_to__gte=today)
        )
        # Force evaluation so errors surface here, not in template
        announcements = list(announcements)
    except Exception:
        announcements = []
    return {"banner_coupons": coupons, "banner_announcements": announcements}


def nav_categories(request):
    men    = list(Category.objects.filter(gender="men",    is_active=True).order_by("sort_order", "name"))
    women  = list(Category.objects.filter(gender="women",  is_active=True).order_by("sort_order", "name"))
    return {"nav_men_categories": men, "nav_women_categories": women}


def active_offers_context(request):
    try:
        offers = list(_get_active_offers())
    except Exception:
        offers = []

    has_global = False
    prod_ids = set()
    cat_ids = set()

    for offer in offers:
        p_ids = {p.id for p in offer.applicable_products.all()}
        c_ids = {c.id for c in offer.applicable_categories.all()}
        if not p_ids and not c_ids:
            has_global = True
        prod_ids |= p_ids
        cat_ids |= c_ids

    return {
        'has_global_offer': has_global,
        'offer_product_ids': prod_ids,
        'offer_category_ids': cat_ids,
    }


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
