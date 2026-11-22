import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from bapu import library
from bapu.api import app
from bapu.music import score_to_musicxml
from bapu.music.model import NoteEvent, Score


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_bundled_scores(self):
        items = library.list_scores()
        self.assertGreaterEqual(len(items), 8)
        self.assertTrue(any("卡农" in item["title"] for item in items))
        classical = library.list_scores(tag="古典")
        self.assertTrue(classical)

    def test_load_score(self):
        score = library.load_score("卡农")
        self.assertTrue(score.piano)
        self.assertTrue(any(event.staff == 1 for event in score.events))

    def test_jasmine_melody(self):
        score = library.load_score("茉莉花")
        melody = [event.midi for event in score.events if event.staff == 0]
        self.assertEqual(melody[:6], [64, 64, 67, 69, 72, 72])
        chords = [event for event in score.events if event.staff == 1]
        self.assertEqual([event.start_beat for event in chords[:3]], [0.0, 0.0, 0.0])

    def test_search_endpoint(self):
        response = self.client.get("/api/search", params={"q": "贝多芬", "providers": "local"})
        self.assertEqual(response.status_code, 200)
        items = response.json()["items"]
        self.assertTrue(any("贝多芬" in item["composer"] for item in items))

    def test_score_detail_endpoint(self):
        response = self.client.get("/api/scores/卡农")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["piano"])
        self.assertTrue(data["musicxml"].startswith("<?xml"))
        self.assertEqual(self.client.get("/api/scores/missing").status_code, 404)

    def test_save_score(self):
        score = Score(title="编辑后的曲谱", piano=True, events=[NoteEvent(72, 0, 1)])
        with tempfile.TemporaryDirectory() as folder:
            user_dir = Path(folder) / "user"
            user_dir.mkdir()
            with patch.object(library, "SCORES_DIR", Path(folder)), \
                 patch.object(library, "USER_DIR", user_dir), \
                 patch.object(library, "USER_INDEX_FILE", user_dir / "index.json"):
                meta = library.save_score("local-key", score)
                again = library.load_score(meta["key"])
                self.assertEqual(again.title, "编辑后的曲谱")
        self.assertEqual(len([item for item in again.events if item.midi is not None]), 1)

    def test_import_mxl(self):
        score = Score(title="MXL Test", piano=True, events=[NoteEvent(72, 0, 2)])
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("score.musicxml", score_to_musicxml(score))
        with tempfile.TemporaryDirectory() as folder:
            user_dir = Path(folder) / "user"
            user_dir.mkdir()
            with patch.object(library, "USER_DIR", user_dir), \
                 patch.object(library, "USER_INDEX_FILE", user_dir / "index.json"):
                meta = library.import_score("test.mxl", buffer.getvalue())
        self.assertEqual(meta["title"], "MXL Test")

    def test_search_falls_back_in_priority_order(self):
        with patch.object(library, "list_scores", return_value=[]), \
             patch.object(library, "_openscore_search", return_value=[]), \
             patch.object(library, "_mutopia_search", return_value=[
                 {"title": "Mutopia Piece", "url": "https://example.org/m", "source": "mutopia"}]), \
             patch.object(library, "_imslp_search", return_value=[
                 {"title": "IMSLP Piece", "url": "https://example.org/i", "source": "imslp"}]) as imslp:
            results = library.search_all("not-found", ["local", "openscore", "mutopia", "imslp"], 5)
        self.assertEqual([item["source"] for item in results], ["mutopia"])
        imslp.assert_not_called()

    def test_search_stops_when_limit_filled(self):
        with patch.object(library, "list_scores", return_value=[
            {"title": "Local", "key": "local", "file": "x.musicxml", "source": "local"}]), \
             patch.object(library, "_openscore_search", return_value=[]) as openscore:
            results = library.search_all("x", ["local", "openscore"], 1)
        self.assertEqual(len(results), 1)
        openscore.assert_not_called()

    def test_import_endpoint(self):
        score = Score(title="我的练习曲", piano=True, events=[
            NoteEvent(72, 0, 1), NoteEvent(48, 0, 1, staff=1),
        ])
        with tempfile.TemporaryDirectory() as folder:
            user_dir = Path(folder) / "user"
            user_dir.mkdir()
            user_index = user_dir / "index.json"
            with patch.object(library, "USER_DIR", user_dir), \
                 patch.object(library, "USER_INDEX_FILE", user_index):
                response = self.client.post(
                    "/api/scores/import",
                    files={"file": ("mine.musicxml", score_to_musicxml(score).encode("utf-8"))},
                )
                self.assertEqual(response.status_code, 200)
                key = response.json()["key"]
                self.assertTrue(any(item["key"] == key for item in library.all_scores()))


if __name__ == "__main__":
    unittest.main()
