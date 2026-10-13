#!/usr/bin/env python3
"""공유 포트가 공유한 파일 (과 그 md 가 참조한 이미지) 말고는 아무것도 내주지 않는지 확인.

  python3 test_share.py
"""
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import docs_viewer as dv


class ShareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        root = base / "root"
        (root / "img").mkdir(parents=True)
        (root / "doc.md").write_text(
            "# 제목\n\n![a](img/a.png)\n\n![b](secret.txt)\n\n<script>alert(1)</script>\n",
            "utf-8")
        (root / "img" / "a.png").write_bytes(b"\x89PNG fake")
        (root / "secret.txt").write_text("secret", "utf-8")
        (root / "other.pdf").write_bytes(b"%PDF fake")
        (base / "outside.txt").write_text("outside", "utf-8")
        cls.root, cls.base = root, base
        dv.SHARES = base / "shares.json"
        dv.CFG.roots = dv.make_roots([str(root)])
        dv.CFG.share_port = 1
        cls.srv = dv.Server(("127.0.0.1", 0), dv.ShareHandler)
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def get(self, path, data=None):
        try:
            with urllib.request.urlopen(self.url + path, data=data) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, ""

    def test_md_and_images(self):
        tok = dv.share_add("r0", "doc.md")
        self.assertEqual(tok, dv.share_add("r0", "doc.md"))     # 같은 파일은 같은 링크
        code, body = self.get("/s/" + tok)
        self.assertEqual(code, 200)
        self.assertIn("제목", body)
        self.assertNotIn("<script>alert", body)                  # 원본 HTML 은 살균
        self.assertNotIn(str(self.root), body)                   # 내 경로가 드러나지 않음
        self.assertEqual(self.get("/s/%s/0" % tok)[0], 200)      # 참조한 이미지
        self.assertEqual(self.get("/s/%s/1" % tok)[0], 404)      # 이미지가 아닌 참조
        self.assertEqual(self.get("/s/%s/2" % tok)[0], 404)      # 참조하지 않은 번호
        dv.share_remove("r0", "doc.md")
        self.assertEqual(self.get("/s/" + tok)[0], 404)

    def test_raw_file(self):
        tok = dv.share_add("r0", "other.pdf")
        self.assertEqual(self.get("/s/" + tok), (200, "%PDF fake"))
        self.assertEqual(self.get("/s/%s/0" % tok)[0], 404)

    def test_nothing_else(self):
        for p in ("/", "/api/config", "/api/tree?root=r0", "/f/r0/secret.txt",
                  "/s/" + "x" * 22, "/s/../secret.txt", "/preview/x", "/office/abc/x"):
            self.assertEqual(self.get(p)[0], 404, p)
        self.assertEqual(self.get("/api/save", data=b"{}")[0], 405)

    def test_symlink_swap(self):
        f = self.root / "swap.txt"
        f.write_text("ok", "utf-8")
        tok = dv.share_add("r0", "swap.txt")
        f.unlink()
        os.symlink(str(self.base / "outside.txt"), str(f))       # 공유 뒤 폴더 밖으로 바꿔치기
        self.assertEqual(self.get("/s/" + tok)[0], 404)


if __name__ == "__main__":
    unittest.main()
