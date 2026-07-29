from datetime import date, datetime, timezone
import unittest

from playlist_builder.events import EventType, NormalizedEvent


class NormalizedEventTests(unittest.TestCase):
    def test_serializes_and_restores_album_drop_event(self) -> None:
        event = NormalizedEvent(
            event_type=EventType.ALBUM_DROP,
            source="spotify",
            title="Example Artist - New Album",
            artist_names=("Example Artist",),
            event_date=date(2026, 6, 16),
            confidence=0.92,
            raw_payload={"spotify_album_id": "album_123"},
            candidate_search_terms=(
                "Example Artist New Album full album",
                "Example Artist 2026 album",
            ),
            detected_at=datetime(2026, 6, 16, 7, 0, tzinfo=timezone.utc),
            source_event_id="album_123",
        )

        restored = NormalizedEvent.from_dict(event.to_dict())

        self.assertEqual(restored, event)
        self.assertEqual(
            event.dedupe_key,
            "spotify:album_drop:album_123",
        )

    def test_rejects_confidence_outside_probability_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "confidence"):
            NormalizedEvent(
                event_type=EventType.FESTIVAL,
                source="festival_research",
                title="Example Festival",
                confidence=1.5,
            )

    def test_rejects_blank_required_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "source"):
            NormalizedEvent(
                event_type=EventType.FESTIVAL,
                source=" ",
                title="Example Festival",
            )

        with self.assertRaisesRegex(ValueError, "title"):
            NormalizedEvent(
                event_type=EventType.FESTIVAL,
                source="festival_research",
                title=" ",
            )

    def test_rejects_non_json_raw_payload(self) -> None:
        with self.assertRaisesRegex(TypeError, "raw_payload"):
            NormalizedEvent(
                event_type=EventType.FESTIVAL,
                source="manual",
                title="Example Festival",
                raw_payload={"seen_at": datetime.now(timezone.utc)},
            )

    def test_generates_fallback_dedupe_key_without_source_id(self) -> None:
        event = NormalizedEvent(
            event_type="festival",
            source="festival_research",
            title="Example Festival",
            artist_names=("Example Artist",),
            event_date=date(2026, 6, 16),
        )

        self.assertEqual(
            event.dedupe_key,
            "festival_research:festival:2026-06-16:example artist:example festival",
        )

if __name__ == "__main__":
    unittest.main()
