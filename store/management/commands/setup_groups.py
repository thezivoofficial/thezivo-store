from django.core.management.base import BaseCommand
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType


def _perms(*codenames):
    return list(Permission.objects.filter(codename__in=codenames))


class Command(BaseCommand):
    help = "Create predefined staff permission groups for the Zivo admin."

    GROUPS = {
        "Order Manager": [
            # Orders
            "view_order", "change_order", "delete_order",
            # Order items (view only)
            "view_orderitem",
            # Customer (view only — for lookup)
            "view_customer",
            # Address (view only)
            "view_address",
        ],
        "Product Manager": [
            # Products
            "view_product", "add_product", "change_product", "delete_product",
            # SKUs
            "view_sku", "add_sku", "change_sku", "delete_sku",
            # Product images
            "view_productimage", "add_productimage", "change_productimage", "delete_productimage",
        ],
        "Inventory Manager": [
            # Only update stock on existing SKUs — no add/delete
            "view_sku", "change_sku",
            "view_product",
            "view_stocknotification",
        ],
        "Store Viewer": [
            # Read-only access to everything
            "view_order", "view_orderitem",
            "view_product", "view_sku", "view_productimage",
            "view_customer", "view_address",
            "view_stocknotification",
        ],
    }

    def handle(self, *args, **options):
        for group_name, codenames in self.GROUPS.items():
            group, created = Group.objects.get_or_create(name=group_name)
            perms = _perms(*codenames)
            group.permissions.set(perms)
            action = "Created" if created else "Updated"
            self.stdout.write(
                self.style.SUCCESS(f"  {action}: {group_name} ({len(perms)} permissions)")
            )

        self.stdout.write(self.style.SUCCESS("\nDone. Assign roles in Admin > Admin Users."))
