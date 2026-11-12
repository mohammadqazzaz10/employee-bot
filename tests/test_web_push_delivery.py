from pathlib import Path


def test_background_web_push_is_wired_and_uses_authenticated_subscriptions():
    app = Path("web_app.py").read_text(encoding="utf-8")
    delivery = Path("web_push_delivery.py").read_text(encoding="utf-8")
    frontend = Path("web/notifications.js").read_text(encoding="utf-8")
    worker = Path("web/sw.js").read_text(encoding="utf-8")

    assert "register_push_delivery(app, auth_required, _json_error)" in app
    assert '@app.get("/api/push/config")' in delivery
    assert '@app.post("/api/push/subscriptions")' in delivery
    assert "g.current_user[\"id\"]" in delivery
    assert "LIMIT 20\n                   FOR UPDATE OF q SKIP LOCKED" in delivery
    assert "vapid_private_key=private_key" in delivery
    assert 'registration.pushManager.subscribe' in frontend
    assert 'self.addEventListener("push"' in worker
    assert "showNotification(title" in worker


def test_web_push_requires_both_vapid_keys():
    delivery = Path("web_push_delivery.py").read_text(encoding="utf-8")
    assert 'VAPID_PUBLIC_KEY' in delivery
    assert 'VAPID_PRIVATE_KEY' in delivery
    assert '"enabled": bool(public_key and private_key)' in delivery
