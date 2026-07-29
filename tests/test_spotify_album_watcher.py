import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from playlist_builder.spotify_album_watcher import (
    SpotifyAPIError,
    SpotifyCredentials,
    SpotifyWebAPIClient,
    load_spotify_env_file,
)


class SpotifyCredentialsAndEnvTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = {
            key: os.environ.get(key)
            for key in (
                "SPOTIFY_CLIENT_ID",
                "SPOTIFY_CLIENT_SECRET",
                "SPOTIFY_ACCESS_TOKEN",
                "SPOTIFY_REFRESH_TOKEN",
            )
        }

    def tearDown(self) -> None:
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_loads_standard_env_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as file:
            file.write(
                "\n".join(
                    [
                        "SPOTIFY_CLIENT_ID=dummy_client",
                        "SPOTIFY_CLIENT_SECRET=dummy_secret",
                        "SPOTIFY_ACCESS_TOKEN=dummy_access",
                    ]
                )
            )
            path = file.name

        try:
            load_spotify_env_file(path)
            credentials = SpotifyCredentials.from_env()
        finally:
            os.unlink(path)

        self.assertEqual(credentials.client_id, "dummy_client")
        self.assertEqual(credentials.client_secret, "dummy_secret")
        self.assertEqual(credentials.access_token, "dummy_access")

    def test_env_file_does_not_override_existing_environment(self) -> None:
        os.environ["SPOTIFY_CLIENT_ID"] = "already_set"

        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as file:
            file.write(
                "\n".join(
                    [
                        "SPOTIFY_CLIENT_ID=file_client",
                        "SPOTIFY_CLIENT_SECRET=file_secret",
                    ]
                )
            )
            path = file.name

        try:
            load_spotify_env_file(path)
            credentials = SpotifyCredentials.from_env()
        finally:
            os.unlink(path)

        self.assertEqual(credentials.client_id, "already_set")
        self.assertEqual(credentials.client_secret, "file_secret")


class SpotifyWebAPIClientRetryTests(unittest.TestCase):
    def test_returns_empty_dict_for_empty_success_response(self) -> None:
        client = SpotifyWebAPIClient(
            SpotifyCredentials(client_id="id", client_secret="secret", access_token="token")
        )
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = b""

        with patch("playlist_builder.spotify_album_watcher.urlopen", return_value=response):
            result = client._open_json(object())

        self.assertEqual(result, {})

    def test_retries_retryable_get_errors(self) -> None:
        client = SpotifyWebAPIClient(
            SpotifyCredentials(client_id="id", client_secret="secret", access_token="token")
        )
        attempts = {"count": 0}

        def flaky(_request):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise SpotifyAPIError("Spotify HTTP 502: bad gateway", status_code=502)
            return {"artists": {"items": []}}

        with patch.object(client, "_open_json", side_effect=flaky), patch(
            "playlist_builder.spotify_album_watcher.time.sleep"
        ) as sleep_mock:
            result = client.search_artists("Madonna")

        self.assertEqual(result, [])
        self.assertEqual(attempts["count"], 3)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_does_not_retry_non_get_requests(self) -> None:
        client = SpotifyWebAPIClient(
            SpotifyCredentials(client_id="id", client_secret="secret", access_token="token")
        )

        with patch.object(
            client,
            "_open_json",
            side_effect=SpotifyAPIError("Spotify HTTP 502: bad gateway", status_code=502),
        ), patch("playlist_builder.spotify_album_watcher.time.sleep") as sleep_mock:
            with self.assertRaises(SpotifyAPIError):
                client.create_playlist(
                    user_id="user_1",
                    name="Test",
                    description="Desc",
                )

        sleep_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
