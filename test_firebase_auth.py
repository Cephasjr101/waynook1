import firebase_auth


def test_unconfigured_firebase_returns_none(monkeypatch):
    monkeypatch.delenv("FIREBASE_SERVICE_ACCOUNT", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    firebase_auth._firebase_tried = False
    firebase_auth._firebase_app = None
    assert firebase_auth.verify_firebase_token("not-a-real-token") is None
