import pytest
from fastapi.testclient import TestClient

import app as app_module
from voice_ai.settings import Settings

TOKEN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

DOCTOR = {
    "name": "Dr. Test",
    "specialization": "General Medicine",
    "department": "OPD",
    "available_days": ["Monday", "Tuesday"],
    "timings": "9 AM - 1 PM",
}


@pytest.fixture
def make_client(redis_store):
    clients = []

    def _make(admin_token: str = TOKEN, store=redis_store):
        app_module.init_services(Settings(load_models=False, admin_token=admin_token), store)
        client = TestClient(app_module.app)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)
    app_module.services = None


@pytest.fixture
def client(make_client):
    return make_client()


def test_pages_are_served(client):
    for path in ("/", "/admin"):
        response = client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


def test_health_reports_state(client):
    data = client.get("/health").json()
    assert data["status"] == "loading"
    assert data["models_loaded"] is False
    assert data["redis_connected"] is True
    assert data["llm_configured"] is False
    assert data["admin_auth_enabled"] is True
    assert set(data["supported_languages"]) == {"kn", "ta", "te", "hi", "en"}


def test_languages(client):
    assert client.get("/languages").json()["languages"]["kn"] == "Kannada"


def test_admin_writes_require_token(client):
    assert client.post("/api/admin/doctors", json=DOCTOR).status_code == 401
    assert (
        client.post("/api/admin/doctors", json=DOCTOR, headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    assert client.delete("/api/admin/faqs/x").status_code == 401
    assert client.post("/api/admin/config", json={"tone": "friendly"}).status_code == 401


def test_admin_reads_are_public(client):
    assert client.get("/api/admin/doctors").json() == {"doctors": []}
    assert client.get("/api/admin/config").json()["hospital_name"] == "Apollo Hospital"


def test_doctor_crud_flow(client):
    created = client.post("/api/admin/doctors", json=DOCTOR, headers=AUTH)
    assert created.status_code == 200
    doc_id = created.json()["id"]

    doctors = client.get("/api/admin/doctors").json()["doctors"]
    assert doctors[0]["name"] == "Dr. Test"
    assert doctors[0]["available_days"] == ["Monday", "Tuesday"]
    assert "consultation_fee" not in doctors[0]

    updated = client.put(
        f"/api/admin/doctors/{doc_id}", json={**DOCTOR, "timings": "2 PM - 6 PM"}, headers=AUTH
    )
    assert updated.status_code == 200
    assert client.get("/api/admin/doctors").json()["doctors"][0]["timings"] == "2 PM - 6 PM"

    assert client.put("/api/admin/doctors/nope", json=DOCTOR, headers=AUTH).status_code == 404
    assert client.delete(f"/api/admin/doctors/{doc_id}", headers=AUTH).status_code == 200
    assert client.delete(f"/api/admin/doctors/{doc_id}", headers=AUTH).status_code == 404


def test_faq_and_department_flow(client):
    faq = client.post("/api/admin/faqs", json={"question": "Parking?", "answer": "Basement."}, headers=AUTH)
    dept = client.post(
        "/api/admin/departments", json={"name": "ER", "floor": "Ground", "timings": "24x7"}, headers=AUTH
    )
    assert faq.status_code == dept.status_code == 200
    assert client.get("/api/admin/faqs").json()["faqs"][0]["answer"] == "Basement."
    assert client.get("/api/admin/departments").json()["departments"][0]["floor"] == "Ground"
    assert client.delete(f"/api/admin/departments/{dept.json()['id']}", headers=AUTH).status_code == 200


def test_validation_errors(client):
    assert client.post("/api/admin/doctors", json={"name": ""}, headers=AUTH).status_code == 422
    assert client.post("/api/admin/config", json={"max_words": 0}, headers=AUTH).status_code == 422


def test_config_update(client):
    response = client.post("/api/admin/config", json={"hospital_name": "Demo", "max_words": 40}, headers=AUTH)
    assert response.status_code == 200
    config = client.get("/api/admin/config").json()
    assert config["hospital_name"] == "Demo"
    assert config["max_words"] == 40
    assert config["emergency_number"] == "108"


def test_admin_open_when_no_token_configured(make_client):
    client = make_client(admin_token="")
    assert client.post("/api/admin/faqs", json={"question": "Q", "answer": "A"}).status_code == 200


def test_admin_writes_need_redis(make_client, offline_store):
    client = make_client(store=offline_store)
    assert (
        client.post("/api/admin/faqs", json={"question": "Q", "answer": "A"}, headers=AUTH).status_code == 503
    )
    assert client.get("/api/admin/faqs").json() == {"faqs": []}


def test_sessions(client):
    session_id = client.post("/api/session").json()["session_id"]
    data = client.get(f"/api/session/{session_id}").json()
    assert data["session_id"] == session_id
    assert data["turn_count"] == 0
    assert client.get("/api/session/doesnotexist").status_code == 404


def test_process_requires_models(client):
    response = client.post("/process", files={"audio": ("a.wav", b"RIFF", "audio/wav")})
    assert response.status_code == 503


def test_websocket_rejects_when_models_not_loaded(client):
    with client.websocket_connect("/ws/call") as ws:
        assert ws.receive_json()["type"] == "error"


def test_escalation_keywords_are_saved(client):
    response = client.post("/api/admin/config", json={"escalation_keywords": ["dog bite"]}, headers=AUTH)
    assert response.status_code == 200
    assert client.get("/api/admin/config").json()["escalation_keywords"] == ["dog bite"]


def test_client_supplied_id_is_ignored_on_create(client, redis_store):
    redis_store.add_faq({"id": "faq001", "question": "Seeded", "answer": "x"})
    response = client.post(
        "/api/admin/faqs", json={"id": "faq001", "question": "Q", "answer": "A"}, headers=AUTH
    )
    assert response.json()["id"] != "faq001"
    assert len(client.get("/api/admin/faqs").json()["faqs"]) == 2


def test_unknown_page_serves_html_404(client):
    response = client.get("/does-not-exist", headers={"accept": "text/html"})
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Page not found" in response.text


def test_unknown_api_path_keeps_json_404(client):
    response = client.get("/api/nope", headers={"accept": "text/html"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert client.get("/does-not-exist").json() == {"detail": "Not Found"}


def test_health_exposes_ui_hints(client):
    data = client.get("/health").json()
    assert data["max_upload_mb"] == 10
    assert data["speech_enabled"] is False
