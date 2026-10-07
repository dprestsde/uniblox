class DatabaseRouter:
    """Keep simulated provider persistence isolated from application transactions."""

    @staticmethod
    def db_for_read(model, **hints):
        return "payments" if model._meta.app_label == "fake_payments" else "default"

    @staticmethod
    def db_for_write(model, **hints):
        return "payments" if model._meta.app_label == "fake_payments" else "default"

    @staticmethod
    def allow_relation(obj1, obj2, **hints):
        first_is_provider = obj1._meta.app_label == "fake_payments"
        second_is_provider = obj2._meta.app_label == "fake_payments"
        return first_is_provider == second_is_provider

    @staticmethod
    def allow_migrate(db, app_label, model_name=None, **hints):
        return db == ("payments" if app_label == "fake_payments" else "default")
