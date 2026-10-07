from django.db import connections


class HealthService:
    def readiness(self):
        for alias in ("default", "payments"):
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        return {"status": "ready"}

    @staticmethod
    def liveness():
        return {"status": "live"}
