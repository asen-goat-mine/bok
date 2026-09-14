"""Content-free, per-Vault controls for automatic background processing."""
from .errors import BokError
from .util import atomic_write_json, read_json


class BackgroundProcessing:
    DEFAULTS = {"paused": False, "batch_limit": 4, "interval_seconds": 30}

    def __init__(self, storage):
        self.storage = storage
        self.path = storage.state / "state" / "background-processing.json"

    @classmethod
    def validate(cls, values):
        if not isinstance(values, dict) or set(values) - set(cls.DEFAULTS):
            raise BokError("invalid_background_settings", "Unknown background processing setting")
        if "paused" in values and type(values["paused"]) is not bool:
            raise BokError("invalid_background_settings", "paused must be a boolean")
        for key, lower, upper in (("batch_limit", 1, 20), ("interval_seconds", 15, 600)):
            if key in values and (type(values[key]) is not int or not lower <= values[key] <= upper):
                raise BokError("invalid_background_settings", f"{key} must be an integer between {lower} and {upper}")

    def status(self):
        values = read_json(self.path, None)
        if values is None and not self.path.exists():
            return dict(self.DEFAULTS)
        try:
            self.validate(values)
        except BokError:
            # A damaged control file must not accidentally resume model work.
            return {**self.DEFAULTS, "paused": True}
        return {**self.DEFAULTS, **values}

    def update(self, values):
        self.validate(values)
        with self.storage.lock:
            result = {**self.status(), **values}
            atomic_write_json(self.path, result)
            return result
