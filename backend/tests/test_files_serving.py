"""PLN-001 WP1: 静态文件服务——封面/附件 HTTP 访问 + 路径穿越防护。"""

from pathlib import Path

from app.config import settings


def _create_cover(name: str = "test_cover.jpg", content: bytes = b"\xff\xd8\xff\xe0fake-jpg"):
    """在 covers_dir 下放一个测试文件。"""
    covers = settings.covers_dir
    covers.mkdir(parents=True, exist_ok=True)
    p = covers / name
    p.write_bytes(content)
    return p


def test_serve_cover_returns_file(client):
    _create_cover("abc.jpg", b"cover-bytes")
    r = client.get("/api/v1/files/covers/abc.jpg")
    assert r.status_code == 200
    assert r.content == b"cover-bytes"


def test_serve_cover_not_found_404(client):
    r = client.get("/api/v1/files/covers/nonexistent.jpg")
    assert r.status_code == 404


def test_serve_cover_path_traversal_rejected(client):
    _create_cover("real.jpg", b"data")
    # 尝试用 ../ 逃出 covers_dir
    r = client.get("/api/v1/files/covers/../../../etc/passwd")
    assert r.status_code == 404


def test_serve_attachment_returns_file(client):
    att_dir = settings.attachments_dir
    att_dir.mkdir(parents=True, exist_ok=True)
    (att_dir / "note.md").write_text("# 笔记")
    r = client.get("/api/v1/files/attachments/note.md")
    assert r.status_code == 200
    assert "# 笔记" in r.text


def test_serve_attachment_subdir_file(client):
    att_dir = settings.attachments_dir
    sub = att_dir / "sub"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "deep.txt").write_text("deep content")
    r = client.get("/api/v1/files/attachments/sub/deep.txt")
    assert r.status_code == 200
    assert "deep content" in r.text
