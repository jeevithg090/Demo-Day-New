from voice_ai.redis_store import serialize_hash


def test_serialize_hash_drops_none_and_encodes_types():
    out = serialize_hash({"id": "x", "a": None, "b": True, "c": [1, "दो"], "d": 3})
    assert out == {"b": "true", "c": '[1, "दो"]', "d": "3"}


def test_offline_store_degrades_gracefully(offline_store):
    assert not offline_store.is_connected
    assert offline_store.get_config()["hospital_name"] == "Apollo Hospital"
    assert offline_store.get_doctors() == []
    assert offline_store.add_doctor({"name": "x"}) is None
    assert offline_store.set_config({}) is False
    assert offline_store.get_session("x") is None


def test_config_roundtrip_preserves_types(redis_store):
    config = redis_store.get_config()
    config.update({"max_words": 70, "disclaimer_required": False, "hospital_name": "Test"})
    assert redis_store.set_config(config)
    loaded = redis_store.get_config()
    assert loaded["max_words"] == 70
    assert loaded["disclaimer_required"] is False
    assert loaded["supported_languages"] == ["kn", "ta", "te", "hi", "en"]
    assert loaded["hospital_name"] == "Test"


def test_partial_config_is_merged_with_defaults(redis_store):
    redis_store.client.hset("hospital:config", mapping={"hospital_name": "Only Name"})
    config = redis_store.get_config()
    assert config["hospital_name"] == "Only Name"
    assert config["emergency_number"] == "108"


def test_doctor_crud(redis_store):
    doc_id = redis_store.add_doctor(
        {
            "name": "Dr. A",
            "specialization": "Cardiology",
            "department": "Heart",
            "available_days": ["Monday"],
            "timings": "9-5",
            "name_kn": None,
        }
    )
    assert doc_id == "d001"
    doctor = redis_store.get_doctor(doc_id)
    assert doctor["available_days"] == ["Monday"]
    assert "name_kn" not in doctor  # None is not stored as "None"

    assert redis_store.update_doctor(doc_id, {"timings": "10-6"})
    assert redis_store.get_doctor(doc_id)["timings"] == "10-6"
    assert not redis_store.update_doctor("missing", {"timings": "x"})

    assert [d["name"] for d in redis_store.search_doctors("cardio")] == ["Dr. A"]
    assert redis_store.get_available_doctors("Monday")[0]["id"] == doc_id
    assert redis_store.get_available_doctors("Sunday") == []

    assert redis_store.delete_doctor(doc_id)
    assert not redis_store.delete_doctor(doc_id)
    assert redis_store.get_doctors() == []


def test_faq_and_department_crud(redis_store):
    faq_id = redis_store.add_faq({"question": "Q?", "answer": "A."})
    dept_id = redis_store.add_department({"name": "ER", "floor": "G", "timings": "24x7"})
    assert [f["id"] for f in redis_store.get_faqs()] == [faq_id]
    assert redis_store.get_departments()[0]["name"] == "ER"
    assert redis_store.update_faq(faq_id, {"answer": "B."})
    assert redis_store.get_faqs()[0]["answer"] == "B."
    redis_store.clear_hospital_data()
    assert redis_store.get_faqs() == [] and redis_store.get_departments() == []
    assert not redis_store.delete_department(dept_id)


def test_session_ttl_follows_config(redis_store):
    config = redis_store.get_config()
    config["session_timeout_minutes"] = 5
    redis_store.set_config(config)
    redis_store.set_session("s1", {"turn_count": 0, "history": []})
    assert 0 < redis_store.client.ttl("session:s1") <= 300
    redis_store.update_session("s1", {"turn_count": 1})
    assert 0 < redis_store.client.ttl("session:s1") <= 300
    assert redis_store.get_session("s1")["turn_count"] == 1


def test_generated_ids_skip_explicitly_seeded_ids(redis_store):
    assert redis_store.add_faq({"id": "faq001", "question": "Seeded", "answer": "x"}) == "faq001"
    new_id = redis_store.add_faq({"question": "New", "answer": "y"})
    assert new_id == "faq002"
    assert {f["question"] for f in redis_store.get_faqs()} == {"Seeded", "New"}
