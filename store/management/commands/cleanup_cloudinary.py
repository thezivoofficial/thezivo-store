"""
Management command: cleanup_cloudinary

Scans Cloudinary folders used by this project and deletes any files
that are no longer referenced in the database.

Usage:
    python manage.py cleanup_cloudinary           # dry run (safe, shows what would be deleted)
    python manage.py cleanup_cloudinary --delete  # actually delete orphaned files
"""

from django.core.management.base import BaseCommand
from store.models import ProductImage


class Command(BaseCommand):
    help = "Delete orphaned Cloudinary files not referenced in the database"

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Actually delete orphaned files (default is dry run)",
        )

    def handle(self, *args, **options):
        dry_run = not options["delete"]

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — no files will be deleted. Pass --delete to actually delete.\n"
            ))

        try:
            import cloudinary
            import cloudinary.api
            import cloudinary.uploader
        except ImportError:
            self.stderr.write("cloudinary package not installed.")
            return

        total_deleted = 0
        total_orphans = 0

        # ── Product images (folder: products/) ───────────────────────────────
        db_image_names = set(
            ProductImage.objects.exclude(image="").values_list("image", flat=True)
        )
        # Strip extensions to get public_ids
        db_public_ids = {name.rsplit(".", 1)[0] for name in db_image_names}

        self.stdout.write("Scanning Cloudinary folder: products/...")
        orphans = self._find_orphans("products", db_public_ids)
        total_orphans += len(orphans)

        for public_id in orphans:
            self.stdout.write(f"  Orphan: {public_id}")
            if not dry_run:
                try:
                    cloudinary.uploader.destroy(public_id)
                    total_deleted += 1
                    self.stdout.write(self.style.SUCCESS(f"  Deleted: {public_id}"))
                except Exception as e:
                    self.stderr.write(f"  Failed to delete {public_id}: {e}")

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"Found {total_orphans} orphaned file(s). Run with --delete to remove them."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Deleted {total_deleted} of {total_orphans} orphaned file(s)."
            ))

    def _find_orphans(self, folder, db_public_ids):
        """Return list of Cloudinary public_ids in a folder not present in db_public_ids."""
        import cloudinary.api

        orphans = []
        next_cursor = None

        while True:
            kwargs = {"type": "upload", "max_results": 500, "prefix": folder + "/"}
            if next_cursor:
                kwargs["next_cursor"] = next_cursor

            try:
                result = cloudinary.api.resources(**kwargs)
            except Exception as e:
                self.stderr.write(f"Cloudinary API error for folder '{folder}': {e}")
                break

            for resource in result.get("resources", []):
                public_id = resource["public_id"]
                if public_id not in db_public_ids:
                    orphans.append(public_id)

            next_cursor = result.get("next_cursor")
            if not next_cursor:
                break

        return orphans
