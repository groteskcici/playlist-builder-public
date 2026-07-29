"""Tests for persistence environment helpers."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from playlist_builder.persistence.env import database_url_from_env


class PersistenceEnvTests(unittest.TestCase):
    def test_database_url_from_env(self) -> None:
        url = "sqlite:///data/playlist_builder.db"
        with patch.dict(os.environ, {"DATABASE_URL": url}, clear=False):
            self.assertEqual(database_url_from_env(), url)


if __name__ == "__main__":
    unittest.main()
