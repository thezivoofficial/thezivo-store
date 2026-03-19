"""
Management command: cleanup_cloudinary

Does two things:
1. Deletes product images from Cloudinary that are no longer referenced in the DB
2. Deletes return proof videos older than 30 days from the return_videos/ folder

Usage:
    python manage.py cleanup_cloudinary           # dry run (safe, shows what would be deleted)
    python manage.py cleanup_cloudinary --delete  # actually delete files
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from store.models import ProductImage


class Command(BaseCommand):
    help = "Delete orphaned product images and old return videos from Cloudinary"

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Actually delete files (default is dry run)",
        )
        parser.add_argument(
            "--video-days",
            type=int,
            default=30,
            help="Delete return videos older than this many days (default: 30)",
        )

    def handle(self, *args, **options):
        dry_run = not options["delete"]
        video_days = options["video_days"]

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "DRY RUN — no files will be deleted. Pass --delete to actually delete.\n"
            ))

        try:
            import cloudinary.api
            import cloudinary.uploader
        except ImportError:
            self.stderr.write("cloudinary package not installed.")
            return

        total_deleted = 0
        total_found = 0

        # ── 1. Orphaned product images (folder: products/) ────────────────────
        self.stdout.write("Scanning Cloudinary folder: products/...")
        db_image_names = set(
            ProductImage.objects.exclude(image="").values_list("image", flat=True)
        )
        db_public_ids = {name.rsplit(".", 1)[0] for name in db_image_names}

        orphans = self._list_folder("products", resource_type="image")
        for resource in orphans:
            public_id = resource["public_id"]
            if public_id not in db_public_ids:
                total_found += 1
                self.stdout.write(f"  Orphan image: {public_id}")
                if not dry_run:
                    try:
                        cloudinary.uploader.destroy(public_id, resource_type="image")
                        total_deleted += 1
                        self.stdout.write(self.style.SUCCESS(f"  Deleted: {public_id}"))
                    except Exception as e:
                        self.stderr.write(f"  Failed: {public_id} — {e}")

        # ── 2. Return videos older than N days (folder: return_videos/) ───────
        cutoff = timezone.now() - timezone.timedelta(days=video_days)
        self.stdout.write(f"\nScanning Cloudinary folder: return_videos/ (older than {video_days} days)...")

        videos = self._list_folder("return_videos", resource_type="video")
        for resource in videos:
            from datetime import datetime, timezone as dt_tz
            created_at = datetime.strptime(
                resource["created_at"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=dt_tz.utc)

            if created_at < cutoff:
                public_id = resource["public_id"]
                total_found += 1
                self.stdout.write(f"  Old video ({resource['created_at']}): {public_id}")
                if not dry_run:
                    try:
                        cloudinary.uploader.destroy(public_id, resource_type="video")
                        total_deleted += 1
                        self.stdout.write(self.style.SUCCESS(f"  Deleted: {public_id}"))
                    except Exception as e:
                        self.stderr.write(f"  Failed: {public_id} — {e}")

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"Found {total_found} file(s) to clean up. Run with --delete to remove them."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"Deleted {total_deleted} of {total_found} file(s)."
            ))

    def _list_folder(self, folder, resource_type="image"):
        """Return all Cloudinary resources in a folder (handles pagination)."""
        import cloudinary.api

        resources = []
        next_cursor = None

        while True:
            kwargs = {
                "type": "upload",
                "resource_type": resource_type,
                "max_results": 500,
                "prefix": folder + "/",
            }
            if next_cursor:
                kwargs["next_cursor"] = next_cursor

            try:
                result = cloudinary.api.resources(**kwargs)
            except Exception as e:
                self.stderr.write(f"Cloudinary API error for '{folder}': {e}")
                break

            resources.extend(result.get("resources", []))
            next_cursor = result.get("next_cursor")
            if not next_cursor:
                break

        return resources
