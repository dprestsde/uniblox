from .settings import *  # noqa: F403

DATABASES["default"]["TEST"] = {"NAME": "test_uniblox"}  # noqa: F405
DATABASES["payments"]["TEST"] = {"NAME": "test_uniblox_payments"}  # noqa: F405
