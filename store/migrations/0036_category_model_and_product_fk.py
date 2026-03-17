"""
Migration 0036: Add Category model + migrate Product.category CharField → ForeignKey

Steps:
1. Create Category table
2. Populate default categories from existing CATEGORY_CHOICES
3. Add category_fk (nullable FK) to Product
4. Populate FK from old CharField values
5. Make FK non-nullable
6. Drop old category CharField
7. Rename category_fk → category
"""
import django.db.models.deletion
from django.db import migrations, models

INITIAL_CATEGORIES = [
    ("shirts",  "Shirts",       "men",   1),
    ("tshirts", "T-Shirts",     "men",   2),
    ("jeans",   "Jeans",        "men",   3),
    ("ethnic",  "Ethnic Wear",  "men",   4),
    ("kurtis",  "Kurtis",       "women", 1),
    ("dresses", "Dresses",      "women", 2),
    ("tops",    "Tops",         "women", 3),
]


def create_categories(apps, schema_editor):
    Category = apps.get_model("store", "Category")
    for slug, name, gender, order in INITIAL_CATEGORIES:
        Category.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "gender": gender, "sort_order": order, "is_active": True},
        )


def populate_category_fk(apps, schema_editor):
    Product = apps.get_model("store", "Product")
    Category = apps.get_model("store", "Category")
    slug_map = {c.slug: c for c in Category.objects.all()}
    for product in Product.objects.all():
        cat = slug_map.get(product.category_old)
        if cat:
            product.category_fk = cat
            product.save(update_fields=["category_fk"])


class Migration(migrations.Migration):

    dependencies = [
        ("store", "0035_add_announcement_model"),
    ]

    operations = [
        # 1. Create Category table
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name",       models.CharField(max_length=100)),
                ("slug",       models.SlugField(unique=True, help_text="URL key used in links, e.g. 'tshirts'")),
                ("gender",     models.CharField(max_length=10, blank=True, default="",
                                                choices=[("men","Men"),("women","Women"),("unisex","Unisex")],
                                                help_text="Which nav dropdown this appears under.")),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active",  models.BooleanField(default=True)),
            ],
            options={"ordering": ["gender", "sort_order", "name"],
                     "verbose_name": "Category",
                     "verbose_name_plural": "Categories"},
        ),

        # 2. Populate default categories
        migrations.RunPython(create_categories, migrations.RunPython.noop),

        # 3. Rename old CharField so we can read it in data migration
        migrations.RenameField(
            model_name="product",
            old_name="category",
            new_name="category_old",
        ),

        # 4. Add nullable FK
        migrations.AddField(
            model_name="product",
            name="category_fk",
            field=models.ForeignKey(
                "store.Category",
                null=True, blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="products",
                db_index=True,
            ),
        ),

        # 5. Populate FK from old values
        migrations.RunPython(populate_category_fk, migrations.RunPython.noop),

        # 6. Remove old CharField
        migrations.RemoveField(model_name="product", name="category_old"),

        # 7. Rename FK → category
        migrations.RenameField(
            model_name="product",
            old_name="category_fk",
            new_name="category",
        ),
    ]
