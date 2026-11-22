import unittest

from fastapi.testclient import TestClient

from bapu.api import app
from bapu.music import jianpu_to_score, score_to_pdf
from bapu.music.pdf import _staff_note_y


class PdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_score_to_pdf(self):
        score = jianpu_to_score("1=C 4/4 ♩=120\n1 2 3 4 | 5 6 7 1' | 2' - 1' 7 ||")
        pdf = score_to_pdf(score, title="测试")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 5000)

    def test_staff_note_vertical_position(self):
        bottom = 600.0
        self.assertLess(_staff_note_y(bottom, 28), bottom)      # C4 在最下方线之下
        self.assertEqual(_staff_note_y(bottom, 30), bottom)     # E4 为最下方线
        self.assertEqual(_staff_note_y(bottom, 38), bottom + 32)  # F5 为最上方线

    def test_pdf_download_endpoint(self):
        items = self.client.get("/api/scores").json()["items"]
        self.assertTrue(items)
        data = self.client.get(f"/api/scores/{items[0]['key']}").json()
        response = self.client.get(f"/api/score/{data['id']}/pdf")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
