import json
import pytest
from conftest import SiteClient, ROOT
from core import speech

READ = json.loads((ROOT / "sites/english/banks/read.json").read_text(encoding="utf-8"))
Q = READ["questions"][0]
PCM = b"\0\0" * 16000  # one second of silence


def test_score_normalises_contractions_spelling_and_numbers():
    assert speech.score("I'm Lily. Nice to meet you.", "i am lily nice to meet you")["score"] == 100
    assert speech.score("Let's be friends!", "lets be friends")["score"] == 100
    assert speech.score("We're family.", "were family")["score"] == 100
    assert speech.score("What colour is it?", "what color is it")["score"] == 100
    assert speech.score("I see 3 pens.", "i see three pens")["score"] == 100
    r = speech.score("I'm Lily. Nice to meet you.", "i am lily nice to meet")
    assert r["score"] == 86 and r["pass"] and [w["ok"] for w in r["words"]] == [True] * 5 + [False]
    r = speech.score("This is my pencil box.", "this is my")
    assert r["score"] == 60 and not r["pass"]
    assert speech.score("He's my friend.", "")["score"] == 0
    assert speech.score("He's my friend.", "um he's my my friend yes")["score"] == 100, "extra words do not count against"


@pytest.fixture
def reader(db, monkeypatch):
    db.execute("DELETE FROM limits")
    db.execute("DELETE FROM devices WHERE site='english'")
    db.commit()
    monkeypatch.setenv("ALIYUN_AK_ID", "id")
    monkeypatch.setenv("ALIYUN_AK_SECRET", "secret")
    monkeypatch.setenv("NLS_APPKEY", "app")
    heard = []
    monkeypatch.setattr(speech, "recognize", lambda pcm: heard.pop(0) if heard else Q["text"])
    c = SiteClient("english")
    assert c.activate().status_code == 200
    return c, heard


def post(c, qid=Q["id"], body=PCM):
    return c.c.post(
        "/english/api/speech/read?q=" + qid,
        data=body,
        headers={"X-CSRF-Token": c.token(), "Content-Type": "application/octet-stream"},
    )


def test_speech_scores_the_bank_sentence(reader, db):
    c, heard = reader
    page = c.get("/games/read").get_data(as_text=True)
    assert '"speech": {"url": "/english/api/speech/read"' in page and "carry.js?v=" in page
    heard.append("hes my friend")
    r = post(c)
    assert r.status_code == 200 and r.get_json()["score"] < 100
    assert post(c).get_json()["code"] == "too_fast"
    db.execute("DELETE FROM limits")
    db.commit()
    assert post(c, qid="nope").status_code == 404
    assert post(c, body=b"\0" * 100).get_json()["code"] == "too_short"
    assert post(c, body=b"\0" * (speech.MAX_BYTES + 2)).status_code == 413
    r = post(c)
    assert r.status_code == 200 and r.get_json()["score"] == 100 and r.get_json()["pass"]


def test_speech_needs_a_license_or_trial_and_config(reader, monkeypatch):
    stranger = SiteClient("english")
    assert post(stranger).status_code == 403
    monkeypatch.delenv("NLS_APPKEY")
    c, _ = reader
    assert post(c).get_json()["code"] == "not_configured"
    assert SiteClient("math").c.post("/math/api/speech/read").status_code in (400, 404)


def test_speech_failure_is_reported(reader, monkeypatch):
    c, _ = reader

    def broken(pcm):
        raise speech.SpeechError("down")

    monkeypatch.setattr(speech, "recognize", broken)
    r = post(c)
    assert r.status_code == 502 and r.get_json()["code"] == "speech_failed"


def test_listen_judge_membership_moves_to_read(db):
    from core import schema

    db.execute("DELETE FROM game_memberships WHERE site='english' AND game='read'")
    db.execute("INSERT INTO game_memberships VALUES('english','listen-judge','g3-t1-a')")
    db.execute("DELETE FROM migrations WHERE name='english_read_replaces_listen_judge'")
    schema.migrate(db)
    db.commit()
    rows = db.execute("SELECT game,group_id FROM game_memberships WHERE site='english' AND game IN ('read','listen-judge')").fetchall()
    assert [tuple(r) for r in rows] == [("read", "g3-t1-a")]
