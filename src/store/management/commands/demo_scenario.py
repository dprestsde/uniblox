from django.core.management import call_command
from django.core.management.base import BaseCommand

from store.services.demo import DemoScenarioService


class Command(BaseCommand):
    help = (
        "Run isolated success, payment failure, timeout, lost-response, "
        "and recovery demonstrations."
    )

    def handle(self, *args, **options):
        call_command("seed_demo", verbosity=0)
        for result in DemoScenarioService().run():
            self.stdout.write(result)
