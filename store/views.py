from django.shortcuts import render, get_object_or_404
from .models import Product, SKU, Order, OrderItem, StockNotification, ProductImage, Address, SiteSettings, CartItem, WishlistItem
from django.shortcuts import redirect
from django.http import JsonResponse, HttpResponse
from django.db.models import Min, F, ExpressionWrapper, IntegerField, Sum, Q
import razorpay
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import get_object_or_404, render, redirect
from django.template.loader import render_to_string
from django.contrib import messages
from django.db import transaction
from .models import Customer
from .customer_auth import customer_login, customer_logout, customer_login_required
from .utils import calculate_delivery_and_final
from django.db.models import Exists, OuterRef, Prefetch
from django.views.decorators.http import require_POST



# ─────────────────────────── Cart helpers ───────────────────────────

def get_cart(request):
    """Return cart as {sku_id_str: qty_int} regardless of storage backend."""
    if request.customer:
        return {str(item.sku_id): item.quantity
                for item in CartItem.objects.filter(customer=request.customer)}
    return request.session.get("cart", {})


def set_cart_item(request, sku_id, qty):
    """Set absolute quantity for a SKU. Removes entry if qty <= 0."""
    if request.customer:
        if qty <= 0:
            CartItem.objects.filter(customer=request.customer, sku_id=int(sku_id)).delete()
        else:
            item, _ = CartItem.objects.get_or_create(customer=request.customer, sku_id=int(sku_id))
            item.quantity = qty
            item.save()
    else:
        cart = request.session.get("cart", {})
        if qty <= 0:
            cart.pop(str(sku_id), None)
        else:
            cart[str(sku_id)] = qty
        request.session["cart"] = cart
        request.session.modified = True


def remove_cart_item(request, sku_id):
    """Remove a SKU from cart entirely."""
    if request.customer:
        CartItem.objects.filter(customer=request.customer, sku_id=int(sku_id)).delete()
    else:
        cart = request.session.get("cart", {})
        cart.pop(str(sku_id), None)
        request.session["cart"] = cart
        request.session.modified = True


def clear_cart(request):
    """Empty the entire cart (called after successful order)."""
    if request.customer:
        CartItem.objects.filter(customer=request.customer).delete()
    else:
        request.session["cart"] = {}
        request.session.modified = True


# ─────────────────────────── Wishlist helpers ───────────────────────────

def get_wishlist(request):
    """Return wishlist as list of product_id integers."""
    if request.customer:
        return list(WishlistItem.objects.filter(
            customer=request.customer).values_list("product_id", flat=True))
    return [int(i) for i in request.session.get("wishlist", []) if str(i).isdigit()]


def toggle_wishlist_item(request, product_id):
    """Toggle product in wishlist. Returns 'added' or 'removed'."""
    product_id = int(product_id)
    if request.customer:
        obj, created = WishlistItem.objects.get_or_create(
            customer=request.customer, product_id=product_id)
        if not created:
            obj.delete()
            return "removed"
        return "added"
    wishlist = get_wishlist(request)
    if product_id in wishlist:
        wishlist.remove(product_id)
        status = "removed"
    else:
        wishlist.append(product_id)
        status = "added"
    request.session["wishlist"] = wishlist
    request.session.modified = True
    return status


@customer_login_required
def manage_addresses(request):
    addresses = Address.objects.filter(customer=request.customer)
    return render(request, "store/addresses.html", {"addresses": addresses})


@customer_login_required
def add_address(request):
    if request.method == "POST":
        is_default = request.POST.get("is_default") == "on"

        # If first address, make it default automatically
        if not Address.objects.filter(customer=request.customer).exists():
            is_default = True

        country_code = request.POST.get("country_code", "+91")
        phone = request.POST.get("phone", "").strip()
        full_phone = country_code + phone

        Address.objects.create(
            customer=request.customer,
            name=request.POST.get("name", "").strip(),
            phone=full_phone,
            address=request.POST.get("address", "").strip(),
            city=request.POST.get("city", "").strip(),
            state=request.POST.get("state", "").strip(),
            pincode=request.POST.get("pincode", "").strip(),
            is_default=is_default
        )
        messages.success(request, "Address saved successfully.")
        next_url = request.POST.get("next", "manage_addresses")
        return redirect(next_url)

    return render(request, "store/address_form.html", {"action": "Add"})


@customer_login_required
def edit_address(request, address_id):
    address = get_object_or_404(Address, id=address_id, customer=request.customer)
    next_url = request.GET.get("next") or request.POST.get("next", "manage_addresses")

    if request.method == "POST":
        address.name = request.POST.get("name", "").strip()
        country_code = request.POST.get("country_code", "+91")
        phone = request.POST.get("phone", "").strip()
        address.phone = country_code + phone
        address.address = request.POST.get("address", "").strip()
        address.city = request.POST.get("city", "").strip()
        address.state = request.POST.get("state", "").strip()
        address.pincode = request.POST.get("pincode", "").strip()
        address.is_default = request.POST.get("is_default") == "on"
        address.save()
        messages.success(request, "Address updated.")
        return redirect(next_url)

    return render(request, "store/address_form.html", {
        "action": "Edit",
        "address": address,
        "next": next_url
    })


@customer_login_required
def delete_address(request, address_id):
    address = get_object_or_404(Address, id=address_id, customer=request.customer)
    if request.method == "POST":
        address.delete()
        # If deleted was default, make most recent the new default
        remaining = Address.objects.filter(customer=request.customer).first()
        if remaining and not Address.objects.filter(customer=request.customer, is_default=True).exists():
            remaining.is_default = True
            remaining.save()
        messages.success(request, "Address removed.")
    return redirect("manage_addresses")


@customer_login_required
def set_default_address(request, address_id):
    address = get_object_or_404(Address, id=address_id, customer=request.customer)
    address.is_default = True
    address.save()
    next_url = request.POST.get("next") or request.GET.get("next", "manage_addresses")
    return redirect(next_url)

def get_cart_items(request):

    cart = request.session.get("cart", {})
    items = []

    for sku_id, qty in cart.items():

        try:
            sku = SKU.objects.select_related("product").get(id=sku_id)
        except SKU.DoesNotExist:
            continue

        items.append({
            "sku": sku,
            "quantity": qty,
            "subtotal": sku.selling_price * qty
        })

    return items


def build_cart_context(request):

    from store.models import SKU

    cart = request.session.get("cart", {})

    items = []
    subtotal = 0

    for sku_id, qty in cart.items():

        try:
            sku = SKU.objects.get(id=sku_id)
        except:
            continue

        item_total = sku.selling_price * qty
        subtotal += item_total

        items.append({
            "sku": sku,
            "quantity": qty,
            "subtotal": item_total   # ✅ ADD THIS
        })

    delivery, total, free_remaining = calculate_delivery_and_final(subtotal)

    return {
        "items": items,
        "cart_subtotal": subtotal,
        "cart_total": total,
        "free_delivery_remaining": free_remaining,
        "delivery_charge": delivery
    }


def home(request):

    request.session.pop("open_cart", None)

    products = Product.objects.filter(
        active=True
    ).prefetch_related(
        Prefetch("images", queryset=ProductImage.objects.all())
    ).annotate(
        has_sku=Exists(
            SKU.objects.filter(product=OuterRef("pk"))
        )
    ).filter(has_sku=True)[:8]

    return render(request, "store/home.html", {
        "products": products
    })


def login_view(request):
    if request.customer:
        return redirect("home")
    if request.method == "POST":
        phone    = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")
        customer = Customer.objects.filter(phone=phone, is_active=True).first()
        if customer and customer.check_password(password):
            customer_login(request, customer)
            return redirect("home")
        else:
            messages.error(request, "Invalid phone number or password.")
    return render(request, "store/login.html")


def signup_view(request):
    if request.customer:
        return redirect("home")
    if request.method == "POST":
        name     = request.POST.get("name", "").strip()
        phone    = request.POST.get("phone", "").strip()
        email    = request.POST.get("email", "").strip()
        password = request.POST.get("password", "")
        if Customer.objects.filter(phone=phone).exists():
            messages.error(request, "An account with this phone number already exists.")
        else:
            customer = Customer(name=name, phone=phone, email=email)
            customer.set_password(password)
            customer.save()
            customer_login(request, customer)
            messages.success(request, "Welcome to Zivo!")
            return redirect("home")
    return render(request, "store/signup.html")


def logout_view(request):
    customer_logout(request)
    return redirect("home")


@customer_login_required
def profile(request):
    order_count = Order.objects.filter(customer=request.customer).exclude(
        payment_status="PENDING", payment_method="ONLINE"
    ).count()
    address_count = Address.objects.filter(customer=request.customer).count()
    return render(request, "store/profile.html", {
        "order_count": order_count,
        "address_count": address_count,
    })


@customer_login_required
def update_profile(request):
    if request.method != "POST":
        return redirect("profile")
    customer = request.customer
    name = request.POST.get("name", "").strip()
    email = request.POST.get("email", "").strip()
    if name:
        customer.name = name
    customer.email = email
    customer.save()
    messages.success(request, "Profile updated successfully.")
    return redirect("profile")


@customer_login_required
def my_orders(request):
    orders = Order.objects.filter(
        customer=request.customer
    ).exclude(
        payment_status="PENDING",
        payment_method="ONLINE"
    ).prefetch_related("items__sku__product").order_by("-created_at")
    return render(request, "store/my_orders.html", {
        "orders": orders
    })

@customer_login_required
def notify_me(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    StockNotification.objects.get_or_create(
        customer=request.customer,
        product=product
    )

    messages.success(
        request,
        "You will be notified once this product is back in stock."
    )

    return redirect(request.META.get("HTTP_REFERER", "/"))


def product_detail(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    images = product.images.all()
    skus = SKU.objects.filter(product=product)
    in_stock = any(sku.stock > 0 for sku in skus)
    default_sku = skus.filter(stock__gt=0).first() or skus.first()


    # Build color_variants: {color_name: first_sku_of_that_color}
    color_variants = {}
    for sku in skus:
        if sku.color not in color_variants:
            color_variants[sku.color] = sku

    return render(request, "store/product_detail.html", {
        "product": product,
        "images": images,
        "skus": skus,
        "default_sku": default_sku,
        "in_stock": in_stock,
        "color_variants": color_variants,
    })


def add_to_cart(request):
    if request.method == "POST":
        sku_id = request.POST.get("sku_id")

        if not sku_id:
            return JsonResponse({
                "status": "error",
                "message": "Invalid product selection."
            }, status=400)

        try:
            sku = SKU.objects.get(id=sku_id)
        except SKU.DoesNotExist:
            return JsonResponse({
                "status": "error",
                "message": "Product is unavailable."
            }, status=404)

        quantity = int(request.POST.get("quantity", 1))

        cart = get_cart(request)
        set_cart_item(request, sku_id, cart.get(sku_id, 0) + quantity)
        cart = get_cart(request)

        # ✅ AJAX request → return JSON
        if request.headers.get("x-requested-with") == "XMLHttpRequest":

            context = build_cart_context(request)

            cart_html = render_to_string(
                "store/cart_items_partial.html",
                {"items": context["items"]},
                request=request
            )

            checkout_html = render_to_string(
                "store/checkout_items_partial.html",
                {"items": context["items"]},
                request=request
            )

            cart_summary = render_to_string(
                "store/cart_summary_partial.html",
                context,
                request=request
            )
            
            cart_footer = render_to_string(
                "store/cart_footer_partial.html",
                context,
                request=request
            )


            return JsonResponse({

                "status": "success",

                "cart_html": cart_html,
                "checkout_html": checkout_html,
                "cart_summary": cart_summary,
                "cart_footer": cart_footer,

                "cart_count": sum(cart.values()),

                "cart_subtotal": context["cart_subtotal"],
                "cart_total": context["cart_total"],
                "delivery_charge": context["delivery_charge"],
                "free_delivery_remaining": context["free_delivery_remaining"],
            })


        # fallback (non-AJAX)
        return redirect(request.META.get("HTTP_REFERER") or "home")


def view_cart(request):

    cart = get_cart(request)
    items = []
    total = 0

    for sku_id, qty in cart.items():
        sku = SKU.objects.select_related("product").get(id=sku_id)
        subtotal = sku.selling_price * qty
        total += subtotal
        all_skus = SKU.objects.filter(product=sku.product).order_by("size")
        items.append({
            "sku": sku,
            "quantity": qty,
            "subtotal": subtotal,
            "all_skus": all_skus,
        })

    return render(request, "store/cart.html", {
        "items": items,
        "total": total
    })
    
def wishlist_page(request):

    cleaned_ids = get_wishlist(request)

    products = Product.objects.filter(
        id__in=cleaned_ids
    ).prefetch_related("sku_set")

    wishlist_items = []

    for product in products:

        sku = product.sku_set.filter(stock__gt=0).first()

        wishlist_items.append({
            "product": product,
            "sku": sku
        })

    return render(request, "store/wishlist.html", {
        "wishlist_items": wishlist_items
    })



def get_product_skus(request, product_id):

    skus = SKU.objects.filter(product_id=product_id)

    data = []

    for sku in skus:
        data.append({
            "id": sku.id,
            "size": sku.size,
            "color": sku.color,
            "stock": sku.stock,

            # ✅ REQUIRED FOR PRICE DISPLAY
            "mrp": float(sku.mrp),
            "selling_price": float(sku.selling_price),

            "discount": int(
                ((sku.mrp - sku.selling_price) / sku.mrp) * 100
            ) if sku.mrp > sku.selling_price else 0
        })

    return JsonResponse({"skus": data})


    
def update_cart(request):
    if request.method != "POST":
        return JsonResponse({"status": "error"}, status=400)

    sku_id = request.POST.get("sku_id")
    action = request.POST.get("action")

    cart = get_cart(request)

    if not sku_id and request.POST.get("init") != "1":
        return JsonResponse({"status": "error"}, status=400)

    # ---------------- UPDATE CART ----------------
    if action == "increase":
        try:
            sku_obj = SKU.objects.get(id=int(sku_id))
        except SKU.DoesNotExist:
            return JsonResponse({"status": "error"}, status=404)
        current_qty = cart.get(sku_id, 0)
        if current_qty >= sku_obj.stock:
            return JsonResponse({
                "status": "stock_exceeded",
                "max_stock": sku_obj.stock,
                "message": f"Only {sku_obj.stock} item{'s' if sku_obj.stock != 1 else ''} available in this size."
            })
        set_cart_item(request, sku_id, current_qty + 1)

    elif action == "decrease":
        current_qty = cart.get(sku_id, 0)
        set_cart_item(request, sku_id, current_qty - 1)

    elif action == "delete":
        remove_cart_item(request, sku_id)

    cart = get_cart(request)

    # ---------------- RECALCULATE ----------------
    items = []
    subtotal = 0
    total_qty = 0

    for sid, qty in cart.items():
        sku = SKU.objects.get(id=int(sid))
        item_subtotal = sku.selling_price * qty
        subtotal += item_subtotal
        total_qty += qty

        items.append({
            "sku": sku,
            "quantity": qty,
            "subtotal": item_subtotal
        })

    delivery_charge, final_amount, remaining = calculate_delivery_and_final(subtotal)

    # ---------------- AJAX (SIDE CART) ----------------
    delivery_charge, final_amount, remaining = calculate_delivery_and_final(subtotal)

    cart_html = render_to_string(
        "store/cart_items_partial.html",
        {"items": items},
        request=request
    )

    checkout_html = render_to_string(
        "store/checkout_items_partial.html",
        {"items": items},
        request=request
    )

    cart_summary = render_to_string(
        "store/cart_summary_partial.html",
        {
            "items": items,
            "cart_subtotal": subtotal,
            "cart_total": final_amount,
            "delivery_charge": delivery_charge,
            "free_delivery_remaining": remaining,
        },
        request=request
    )

    cart_footer = render_to_string(
        "store/cart_footer_partial.html",
        {
            "items": items,
            "cart_subtotal": subtotal,
            "cart_total": final_amount,
            "delivery_charge": delivery_charge,
            "free_delivery_remaining": remaining,
        },
        request=request
    )


    return JsonResponse({

        "status": "success",

        "cart_html": cart_html,
        "checkout_html": checkout_html,
        "cart_summary": cart_summary,
        "cart_footer": cart_footer,

        "cart_count": total_qty,

        "cart_subtotal": subtotal,
        "cart_total": final_amount,
        "delivery_charge": delivery_charge,
        "free_delivery_remaining": remaining,
    })


def calculate_cart_total(cart):
    
    total = 0
    for sku_id, qty in cart.items():
        try:
            sku = SKU.objects.get(id=int(sku_id))
            total += sku.selling_price * qty
        except SKU.DoesNotExist:
            continue
    return total
    
    
def checkout(request):
    cart = get_cart(request)

    # 🔴 Cart empty check
    if not cart:
        messages.error(request, "Your cart is empty.")
        return redirect("view_cart")

    items = []
    total = 0

    # ---------- BUILD CART ----------
    for sku_id, qty in cart.items():
        try:
            sku = SKU.objects.get(id=sku_id)
        except SKU.DoesNotExist:
            messages.error(request, "Some products are no longer available.")
            return redirect("view_cart")

        if qty > sku.stock:
            messages.error(
                request,
                f"Only {sku.stock} left for {sku.product.name} ({sku.size})."
            )
            return redirect("view_cart")

        subtotal = sku.selling_price * qty
        total += subtotal

        items.append({
            "sku": sku,
            "quantity": qty,
            "subtotal": subtotal
        })

    # ✅ DELIVERY & FINAL AMOUNT (ON FULL CART TOTAL)
    delivery_charge, final_amount, remaining = calculate_delivery_and_final(total)

    from .models import SiteSettings
    cod_enabled = SiteSettings.get().cod_enabled

    # Base context reused across all render calls in this view
    _ctx = {
        "items": items,
        "total": total,
        "delivery_charge": delivery_charge,
        "final_amount": final_amount,
        "free_delivery_remaining": remaining,
        "cod_enabled": cod_enabled,
    }

    # ---------- POST ----------
    if request.method == "POST":

        name = request.POST.get("name", "").strip()
        phone = request.POST.get("phone", "").strip()
        country_code = request.POST.get("country_code", "+91").strip()
        address = request.POST.get("address", "").strip()
        payment_method = request.POST.get("payment_method")

        form_data = {
            "name": name,
            "phone": phone,
            "country_code": country_code,
            "address": address,
            "city": request.POST.get("city", ""),
            "pincode": request.POST.get("pincode", ""),
            "payment_method": payment_method
        }

        # ---------- VALIDATIONS ----------
        if not name:
            messages.error(request, "Name is required.")
            return render(request, "store/checkout.html", {**_ctx, "form_data": form_data})

        if not phone.isdigit():
            messages.error(request, "Phone number should contain digits only.")
            return render(request, "store/checkout.html", {**_ctx, "form_data": form_data})

        phone_length = len(phone)
        valid = False

        if country_code == "+91":
            valid = phone_length == 10
        elif country_code == "+1":
            valid = phone_length == 10
        elif country_code == "+44":
            valid = phone_length in (10, 11)
        elif country_code == "+61":
            valid = phone_length == 9
        else:
            valid = 6 <= phone_length <= 15

        if not valid:
            messages.error(
                request,
                f"Enter a valid phone number for country code {country_code}."
            )
            return render(request, "store/checkout.html", {**_ctx, "form_data": form_data})

        if not address:
            messages.error(request, "Address is required.")
            return render(request, "store/checkout.html", {**_ctx, "form_data": form_data})

        full_phone = f"{country_code}{phone}"

        # ---------- COD ----------
        if payment_method == "cod":
            with transaction.atomic():
                order = Order.objects.create(
                    customer=request.customer or None,
                    name=name,
                    phone=full_phone,
                    address=address,
                    city=request.POST.get("city", ""),
                    state=request.POST.get("state", ""),
                    pincode=request.POST.get("pincode", ""),
                    total_amount=final_amount,
                    payment_status="PENDING",
                    payment_method="COD",
                    status="PLACED"
                )

                for item in items:
                    OrderItem.objects.create(
                        order=order,
                        sku=item["sku"],
                        quantity=item["quantity"],
                        price=item["sku"].selling_price
                    )

                    item["sku"].stock -= item["quantity"]
                    item["sku"].save()

            clear_cart(request)
            return redirect("order_success", order_id=order.id)

        # ---------- ONLINE ----------
        if payment_method == "online":
            order = Order.objects.create(
                customer=request.customer or None,
                name=name,
                phone=full_phone,
                address=address,
                city=request.POST.get("city", ""),
                state=request.POST.get("state", ""),
                pincode=request.POST.get("pincode", ""),
                total_amount=final_amount,
                payment_method="ONLINE",
                payment_status="PENDING",
                status="CREATED"
            )

            for item in items:
                OrderItem.objects.create(
                    order=order,
                    sku=item["sku"],
                    quantity=item["quantity"],
                    price=item["sku"].selling_price
                )

            return render(request, "store/checkout.html", {
                **_ctx, "order": order, "online_payment": True
            })

        messages.error(request, "Invalid payment method selected.")
        return redirect("checkout")

    # ---------- GET ----------
    form_data = {}
    saved_addresses = []
    default_address = None

    if request.customer:
        saved_addresses = Address.objects.filter(customer=request.customer)
        default_address = saved_addresses.filter(is_default=True).first()

        if default_address:
            raw_phone = default_address.phone
            detected_code = "+91"
            stripped_phone = raw_phone

            for code in ["+91", "+44", "+61", "+1"]:
                if raw_phone.startswith(code):
                    detected_code = code
                    stripped_phone = raw_phone[len(code):]
                    break

            form_data = {
                "name": default_address.name,
                "phone": stripped_phone,
                "country_code": detected_code,
                "address": default_address.address,
                "city": default_address.city,
                "state": default_address.state,
                "pincode": default_address.pincode,
            }
        else:
            form_data = {"name": request.customer.name}

    return render(request, "store/checkout.html", {
        **_ctx,
        "form_data": form_data,
        "saved_addresses": saved_addresses,
        "default_address": default_address,
    })

    
@csrf_exempt
def create_razorpay_order(request):
    if request.method == "POST":

        order_id = request.POST.get("order_id")
        if not order_id:
            return JsonResponse(
                {"error": "Order ID missing"},
                status=400
            )

        order = Order.objects.get(id=order_id)

        amount = int(order.total_amount * 100)  # paise

        client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )

        razorpay_order = client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": f"order_{order.id}",
            "payment_capture": 1
        })

        order.razorpay_order_id = razorpay_order["id"]
        order.save()

        return JsonResponse({
            "razorpay_order_id": razorpay_order["id"],
            "key": settings.RAZORPAY_KEY_ID,
            "amount": amount,
        })

        
@csrf_exempt
def verify_payment(request):
    if request.method != "POST":
        return JsonResponse({"status": "invalid"}, status=400)

    data = request.POST

    razorpay_order_id = data.get("razorpay_order_id")
    razorpay_payment_id = data.get("razorpay_payment_id")
    razorpay_signature = data.get("razorpay_signature")

    client = razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": razorpay_order_id,
            "razorpay_payment_id": razorpay_payment_id,
            "razorpay_signature": razorpay_signature
        })
    except razorpay.errors.SignatureVerificationError:
        return JsonResponse({"status": "failed"})

    with transaction.atomic():
        order = Order.objects.select_for_update().get(
            razorpay_order_id=razorpay_order_id
        )

        order.razorpay_payment_id = razorpay_payment_id
        order.razorpay_signature = razorpay_signature
        order.payment_status = "PAID"
        order.status = "PLACED"
        order.save()

        # 🔴 Reduce stock
        for item in OrderItem.objects.filter(order=order):
            item.sku.stock -= item.quantity
            item.sku.save()

    # ✅ CLEAR CART HERE (CRITICAL)
    clear_cart(request)

    return JsonResponse({
        "status": "success",
        "order_id": order.id
    })

        

def order_success(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    return render(
        request,
        "store/order_success.html",
        {"order": order}
    )

        
def category_products(request, gender, category=None):

    products = Product.objects.filter(
        active=True,
        gender=gender
    ).prefetch_related(
        Prefetch("images", queryset=ProductImage.objects.all())
    ).annotate(
        has_sku=Exists(
            SKU.objects.filter(product=OuterRef("pk"))
        ),
        min_selling_price=Min("sku__selling_price"),
        min_mrp=Min("sku__mrp"),
        total_stock=Sum("sku__stock"),
    ).filter(
        has_sku=True
    ).annotate(
        discount_percent=ExpressionWrapper(
            (F("min_mrp") - F("min_selling_price")) * 100 / F("min_mrp"),
            output_field=IntegerField()
        )
    )


    if category:
        products = products.filter(category=category)

    # filters from GET
    selected_brands     = request.GET.getlist("brand")
    selected_sizes      = request.GET.getlist("size")
    selected_colors     = request.GET.getlist("color")
    selected_categories = request.GET.getlist("category")
    selected_discount   = request.GET.get("discount")
    min_price = request.GET.get("min_price")
    max_price = request.GET.get("max_price")

    if selected_brands:
        products = products.filter(brand__in=selected_brands)

    if selected_sizes:
        products = products.filter(sku__size__in=selected_sizes).distinct()

    if selected_colors:
        products = products.filter(sku__color__in=selected_colors).distinct()

    if selected_categories:
        products = products.filter(category__in=selected_categories)

    if selected_discount:
        try:
            products = products.filter(discount_percent__gte=int(selected_discount))
        except (ValueError, TypeError):
            pass

    if min_price:
        products = products.filter(sku__selling_price__gte=min_price)

    if max_price:
        products = products.filter(sku__selling_price__lte=max_price)


    # ── Single query for brands + categories (2 Product queries → 1) ──
    product_filter_rows = (
        Product.objects.filter(gender=gender, active=True)
        .values("brand", "category")
    )
    brands = sorted(set(r["brand"] for r in product_filter_rows if r["brand"]))
    CATEGORY_DISPLAY = dict(Product.CATEGORY_CHOICES)
    categories_raw = list(dict.fromkeys(r["category"] for r in product_filter_rows if r["category"]))
    categories = [(c, CATEGORY_DISPLAY.get(c, c.title())) for c in categories_raw]

    # ── Single query for sizes + colors + price range (3 SKU queries → 1) ──
    SIZE_ORDER = ["XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL"]
    sku_filter_rows = list(
        SKU.objects.filter(product__active=True, product__gender=gender)
        .values("size", "color", "selling_price")
    )
    sizes = sorted(
        set(r["size"].strip() for r in sku_filter_rows if r["size"].strip()),
        key=lambda s: SIZE_ORDER.index(s) if s in SIZE_ORDER else 99
    )
    colors = sorted(set(r["color"].strip() for r in sku_filter_rows if r.get("color", "").strip()))
    all_prices = [r["selling_price"] for r in sku_filter_rows if r["selling_price"] is not None]
    price_range = {
        "min_price": min(all_prices) if all_prices else 0,
        "max_price": max(all_prices) if all_prices else 0,
    }

    title = gender.capitalize()
    if category:
        title = f"{title} - {category.replace('-', ' ').title()}"
        
    sort = request.GET.get("sort")
    if sort == "low":
        products = products.order_by("min_selling_price")
    elif sort == "high":
        products = products.order_by("-min_selling_price")
    else:
        products = products.order_by("-id")
        
    # ✅ AJAX RESPONSE
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        html = render_to_string(
            "store/product_grid.html",
            {"products": products},
            request=request
        )
        return JsonResponse({"html": html})


    return render(request, "store/category.html", {
        "products": products,
        "title": title,
        "brands": brands,
        "sizes": sizes,
        "colors": colors,
        "categories": categories,
        "selected_brands": selected_brands,
        "selected_sizes": selected_sizes,
        "selected_colors": selected_colors,
        "selected_categories": selected_categories,
        "selected_discount": selected_discount,
        "url_category": category,
        "discount_options": [10, 20, 30, 40, 50],
        "min_price_db": price_range["min_price"] or 0,
        "max_price_db": price_range["max_price"] or 0,
        "selected_min_price": min_price,
        "selected_max_price": max_price,
    })
    

def toggle_wishlist(request):

    if request.method != "POST":
        return JsonResponse({"status": "error"})

    product_id = request.POST.get("product_id")

    if not product_id or not str(product_id).isdigit():
        return JsonResponse({"status": "error"})

    product_id = int(product_id)

    status = toggle_wishlist_item(request, product_id)
    wishlist = get_wishlist(request)

    return JsonResponse({
        "status": status,
        "count": len(wishlist),
        "product_id": product_id
    })
    
    
def search_products(request):
    query = request.GET.get("q", "").strip()

    products = Product.objects.filter(
        active=True
    ).prefetch_related(
        Prefetch("images", queryset=ProductImage.objects.all())
    ).annotate(

        has_sku=Exists(
            SKU.objects.filter(product=OuterRef("pk"))
        )
    ).filter(has_sku=True)


    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(brand__icontains=query) |
            Q(category__icontains=query)
        )

    # same annotations used in category listing
    products = products.annotate(
        min_selling_price=Min("sku__selling_price"),
        min_mrp=Min("sku__mrp"),
        total_stock=Sum("sku__stock"),
    ).annotate(
        discount_percent=ExpressionWrapper(
            (F("min_mrp") - F("min_selling_price")) * 100 / F("min_mrp"),
            output_field=IntegerField()
        )
    ).order_by("-id")

    # AJAX support (future-proof)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        html = render_to_string(
            "store/product_grid.html",
            {"products": products},
            request=request
        )
        return JsonResponse({"html": html})

    return render(request, "store/search_results.html", {
        "products": products,
        "query": query,
        "title": f"Search results for '{query}'"
    })


def search_suggest(request):
    query = request.GET.get("q", "").strip()

    if len(query) < 2:
        return JsonResponse({"results": []})

    products = (
        Product.objects
        .filter(active=True)
        .annotate(
            has_sku=Exists(
                SKU.objects.filter(product=OuterRef("pk"))
            )
        )
        .filter(has_sku=True)
        .filter(
            Q(name__icontains=query) |
            Q(brand__icontains=query) |
            Q(category__icontains=query)
        )
        .annotate(min_price=Min("sku__selling_price"))
        .values(
            "id",
            "name",
            "brand",
            "category",
            "image",
            "min_price"
        )[:8]
    )


    results = []
    for p in products:
        results.append({
            "id": p["id"],
            "name": p["name"],
            "brand": p["brand"],
            "category": p["category"],
            "price": p["min_price"],
            "image": p["image"],
            "url": f"/product/{p['id']}/"
        })

    return JsonResponse({"results": results})

@require_POST
def change_cart_size(request):

    old_sku_id = request.POST.get("old_sku_id")
    new_sku_id = request.POST.get("new_sku_id")

    cart = get_cart(request)

    # Safety
    if not old_sku_id or not new_sku_id:
        return JsonResponse({"status": "error"})

    old_sku_id = str(old_sku_id)
    new_sku_id = str(new_sku_id)

    if old_sku_id not in cart:
        return JsonResponse({"status": "error"})

    qty = cart[old_sku_id]
    remove_cart_item(request, old_sku_id)
    set_cart_item(request, new_sku_id, cart.get(new_sku_id, 0) + qty)
    cart = get_cart(request)

    # ---------- Rebuild Cart ----------
    items = []
    subtotal = 0
    total_qty = 0

    for sid, q in cart.items():

        sku = SKU.objects.get(id=int(sid))

        item_total = sku.selling_price * q

        subtotal += item_total
        total_qty += q

        items.append({
            "sku": sku,
            "quantity": q,
            "subtotal": item_total
        })


    delivery, final_total, remaining = calculate_delivery_and_final(subtotal)


    # ---------- Render Side Cart ----------
    cart_html = render_to_string(
        "store/cart_items_partial.html",
        {"items": items},
        request=request
    )


    # ---------- Render Checkout ----------
    checkout_html = render_to_string(
        "store/checkout_items_partial.html",
        {"items": items},
        request=request
    )

    cart_footer = render_to_string(
        "store/cart_footer_partial.html",
        {
            "items": items,
            "cart_subtotal": subtotal,
            "cart_total": final_total,
            "delivery_charge": delivery,
            "free_delivery_remaining": remaining,
        },
        request=request
    )

    cart_summary = render_to_string(
        "store/cart_summary_partial.html",
        {
            "items": items,
            "cart_subtotal": subtotal,
            "cart_total": final_total,
            "delivery_charge": delivery,
            "free_delivery_remaining": remaining,
        },
        request=request
    )

    return JsonResponse({

        "status": "success",

        "cart_html": cart_html,
        "checkout_html": checkout_html,
        "cart_footer": cart_footer,
        "cart_summary": cart_summary,

        "cart_count": total_qty,

        "cart_subtotal": subtotal,
        "delivery_charge": delivery,
        "cart_total": final_total,

        "free_delivery_remaining": remaining,
    })


def cart_ajax(request):
    cart = get_cart(request)

    items = []
    subtotal = 0

    from .models import SKU

    for sku_id, qty in cart.items():
        try:
            sku = SKU.objects.get(id=sku_id)
        except SKU.DoesNotExist:
            continue

        item_total = sku.selling_price * qty
        subtotal += item_total

        items.append({
            "sku": sku,
            "quantity": qty,
            "subtotal": item_total  # ✅ FIX 1: was item_total, must be subtotal
        })

    delivery, total, remaining = calculate_delivery_and_final(subtotal)

    # Render partials
    cart_html = render_to_string(
        "store/cart_items_partial.html",
        {"items": items},
        request=request
    )

    checkout_html = render_to_string(
        "store/checkout_items_partial.html",
        {"items": items},
        request=request
    )

    cart_footer = render_to_string(
        "store/cart_footer_partial.html",
        {
            "items": items,
            "cart_subtotal": subtotal,
            "cart_total": total,
            "delivery_charge": delivery,
            "free_delivery_remaining": remaining,
        },
        request=request
    )

    return JsonResponse({
        "status": "success",
        "cart_html": cart_html,           # ✅ FIX 2: was html
        "checkout_html": checkout_html,
        "cart_footer": cart_footer,
        "cart_subtotal": subtotal,
        "cart_total": total,
        "delivery_charge": delivery,
        "cart_count": sum(int(q) for q in cart.values()),
        "free_delivery_remaining": remaining,
    })


@customer_login_required
def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id, customer=request.customer)
    return render(request, "store/order_detail.html", {"order": order})


@customer_login_required
def download_invoice(request, order_id):
    from weasyprint import HTML

    order = get_object_or_404(Order, id=order_id, customer=request.customer)
    items = order.items.select_related("sku__product").all()
    subtotal = sum(item.price * item.quantity for item in items)
    delivery, total, _ = calculate_delivery_and_final(subtotal)

    html = render_to_string("store/customer_invoice.html", {
        "order":    order,
        "items":    items,
        "subtotal": subtotal,
        "delivery": delivery,
        "total":    total,
        "store":    SiteSettings.get(),
    }, request=request)

    pdf = HTML(string=html).write_pdf()
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Invoice-Order-{order.id}.pdf"'
    return response


@customer_login_required
def cancel_order(request, order_id):
    if request.method != "POST":
        return redirect("my_orders")

    order = get_object_or_404(Order, id=order_id, customer=request.customer)

    # Only allow cancel if not yet shipped
    if order.status in ["PLACED", "CREATED"]:
        # Restore stock
        for item in order.items.all():
            item.sku.stock += item.quantity
            item.sku.save()

        order.status = "CANCELLED"
        order.save()
        messages.success(request, f"Order #{order.id} has been cancelled.")
    else:
        messages.error(request, f"Order #{order.id} cannot be cancelled as it is already {order.status.lower()}.")

    return redirect("my_orders")