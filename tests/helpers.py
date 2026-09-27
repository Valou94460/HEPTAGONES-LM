"""Utilitaires de test : base SQLite en mémoire et configuration minimale."""

from pathlib import Path

from heptagoneslm.config import parse_config
from heptagoneslm.storage import Database, create_schema

FIXTURES = Path(__file__).parent / "fixtures"


def make_config(**server_overrides):
    server = {
        "name": "AUTODESK",
        "port_at_host": "27000@licsrv1",
        "collectors": ["lmstat"],
        "sessions_from": "lmstat",
    }
    server.update(server_overrides)
    return parse_config(
        {
            "database": {"connection_string": "sqlite:///:memory:"},
            "servers": [server],
            "products": [
                {"pattern": "87545ACD_2024_*", "product": "AutoCAD 2024", "publisher": "Autodesk"},
                {"pattern": "87048INVPROSA_*", "product": "Inventor Pro 2024", "publisher": "Autodesk"},
                {"pattern": "MATLAB", "product": "MATLAB", "publisher": "MathWorks"},
            ],
        }
    )


def make_db():
    db = Database("sqlite:///:memory:")
    create_schema(db)
    return db
