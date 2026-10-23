"""Read-only ledger view plus discarded writes for shadow evaluation."""
from contextlib import contextmanager
import sqlite3

from .storage import StateStore


class ShadowStateStore(StateStore):
    def __init__(self, source: StateStore):
        # Do not initialize/migrate the real schema or copy a large trading DB.
        self.__dict__.update(source.__dict__)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save_position(self, position):
        pass

    def save_lot(self, lot):
        pass

    def save_positions(self, positions):
        pass

    def save_lots(self, lots):
        pass

    def record_decision(self, payload):
        return 0

    def record_decision_context(self, payload, snapshot):
        return 0, 0

    def record_price_snapshot(self, snapshot):
        return 0

    def link_decision_price_snapshot(self, decision_id, snapshot_id):
        pass
