import os
import unittest
from unittest.mock import patch

from playlist_builder.spotify_credentials import publish_slots_from_env


class SpotifyCredentialsEnvTests(unittest.TestCase):
    def test_publish_slots_use_per_account_app_credentials(self) -> None:
        env = {
            "SPOTIFY_CLIENT_ID": "api_app",
            "SPOTIFY_CLIENT_SECRET": "api_secret",
            "SPOTIFY_PUBLISH_1_CLIENT_ID": "pub1_app",
            "SPOTIFY_PUBLISH_1_CLIENT_SECRET": "pub1_secret",
            "SPOTIFY_PUBLISH_1_REFRESH_TOKEN": "pub1_refresh",
            "SPOTIFY_PUBLISH_2_CLIENT_ID": "pub2_app",
            "SPOTIFY_PUBLISH_2_CLIENT_SECRET": "pub2_secret",
            "SPOTIFY_PUBLISH_2_REFRESH_TOKEN": "pub2_refresh",
        }
        with patch.dict(os.environ, env, clear=True):
            slots = publish_slots_from_env()

        self.assertEqual(len(slots), 2)
        self.assertEqual(slots[0].client_id, "pub1_app")
        self.assertEqual(slots[0].client_secret, "pub1_secret")
        self.assertEqual(slots[1].client_id, "pub2_app")
        self.assertEqual(slots[1].client_secret, "pub2_secret")


if __name__ == "__main__":
    unittest.main()
