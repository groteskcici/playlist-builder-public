import json
import tempfile
import unittest
from pathlib import Path

from playlist_builder.spotify_credentials import SpotifyPublishSlot
from playlist_builder.spotify_publish_pool import SpotifyPublishPool


class SpotifyPublishPoolTests(unittest.TestCase):
    def test_alternates_slots_for_new_playlists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            pool = SpotifyPublishPool(
                (
                    SpotifyPublishSlot(
                        slot="1",
                        client_id="app_1",
                        client_secret="secret_1",
                        refresh_token="token_1",
                    ),
                    SpotifyPublishSlot(
                        slot="2",
                        client_id="app_2",
                        client_secret="secret_2",
                        refresh_token="token_2",
                    ),
                ),
                state_path=state_path,
            )

            slots: list[str] = []
            for _ in range(4):
                slot, _ = pool.client_for_new_playlist()
                slots.append(slot)

            self.assertEqual(slots, ["1", "2", "1", "2"])

    def test_persists_round_robin_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            slots = (
                SpotifyPublishSlot(
                    slot="1",
                    client_id="app_1",
                    client_secret="secret_1",
                    refresh_token="token_1",
                ),
                SpotifyPublishSlot(
                    slot="2",
                    client_id="app_2",
                    client_secret="secret_2",
                    refresh_token="token_2",
                ),
            )
            pool = SpotifyPublishPool(slots, state_path=state_path)
            _, _ = pool.client_for_new_playlist()
            pool2 = SpotifyPublishPool(slots, state_path=state_path)
            slot, _ = pool2.client_for_new_playlist()
            self.assertEqual(slot, "2")
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["next_index"], 0)


if __name__ == "__main__":
    unittest.main()
