import csv
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from dispatch.models import Driver


class Command(BaseCommand):
    help = "Import drivers from a CSV file with a driver_name column."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=str(Path(__file__).resolve().parents[4] / "drivers.csv"),
            help="Path to the CSV file.",
        )

    def handle(self, *args, **options):
        csv_path = Path(options["path"]).resolve()
        if not csv_path.exists():
            raise CommandError(f"CSV file not found: {csv_path}")

        created_count = 0
        updated_count = 0

        with csv_path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                name = (row.get("driver_name") or "").strip()
                if not name:
                    continue

                driver, created = Driver.objects.get_or_create(
                    name=name,
                    defaults={
                        "notes": "",
                    },
                )

                if created:
                    created_count += 1
                else:
                    updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported drivers. created={created_count}, existing={updated_count}, path={csv_path}"
            )
        )

