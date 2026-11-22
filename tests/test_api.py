import unittest

from fastapi.testclient import TestClient

from bapu.api import app


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _open_first_score(self):
        items = self.client.get("/api/scores").json()["items"]
        self.assertTrue(items)
        response = self.client.get(f"/api/scores/{items[0]['key']}")
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_health_and_index(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("找到喜欢的曲谱", response.text)
        self.assertNotIn("旧版工具", response.text)
        self.assertEqual(self.client.get("/legacy").status_code, 404)

    def test_legacy_tool_routes_are_removed(self):
        for path in ("/api/demo", "/api/demo-piano", "/api/transcribe",
                     "/api/omr", "/api/play", "/api/scores/save"):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_score_open_and_export(self):
        data = self._open_first_score()
        self.assertTrue(data["id"])
        self.assertTrue(data["events"])
        self.assertTrue(data["jianpu"])

        musicxml = self.client.get(f"/api/score/{data['id']}/musicxml")
        self.assertEqual(musicxml.status_code, 200)
        self.assertIn("score-partwise", musicxml.text)

        pdf = self.client.get(f"/api/score/{data['id']}/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
