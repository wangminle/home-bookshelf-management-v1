"""TST-001: 入库去重——无 ISBN 同书名二次入库命中去重、归一化等价、ISBN-10↔13 互查。"""


def _intake_json(client, **kwargs):
    return client.post("/api/v1/books/intake/json", json=kwargs)


def test_first_intake_creates_new_book(client):
    r = _intake_json(client, title="哈利波特", author="J.K.罗琳")
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["action"] == "created"
    assert d["already_exists"] is False


def test_title_dedup_no_isbn_second_intake_is_exists(client):
    _intake_json(client, title="三体", author="刘慈欣")
    r = _intake_json(client, title="三体", author="刘慈欣")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["action"] == "exists"
    assert d["already_exists"] is True


def test_title_dedup_normalized_equivalence(client):
    """'Harry Potter' 入库后，'Harry  Potter.'（多空格+标点）二次 intake 应命中去重。"""
    _intake_json(client, title="Harry Potter", author="Rowling")
    r = _intake_json(client, title="Harry  Potter.", author="Rowling")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["already_exists"] is True


def test_isbn_dedup_10_matches_13(client):
    """书以 ISBN-13 入库，再用等价 ISBN-10 intake→命中去重（isbn_lookup_keys 互查）。"""
    # 9780306406157 的等价 ISBN-10 是 0306406152
    _intake_json(client, isbn="9780306406157")
    r = _intake_json(client, isbn="0306406152")
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["already_exists"] is True


def test_different_title_different_book(client):
    _intake_json(client, title="书A", author="作者")
    r = _intake_json(client, title="书B", author="作者")
    assert r.status_code == 201, r.text
    assert r.json()["data"]["action"] == "created"


def test_intake_with_location_creates_copy_on_new_book(client):
    r = _intake_json(client, title="带副本的书", author="作者", location="书架1")
    assert r.status_code == 201, r.text
    assert r.json()["data"]["created_copy"] is True
