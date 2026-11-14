import pytest

from voice_ai.conversation import ContextExtractor, ConversationManager


@pytest.fixture
def extractor():
    return ContextExtractor()


def test_extracts_name_age_symptoms_duration(extractor):
    ctx = extractor.extract("My name is Ravi Kumar, I am 45 years old and have fever for 3 days", "en")
    assert ctx["patient_name"] == "Ravi Kumar"
    assert ctx["patient_age"] == 45
    assert "fever" in ctx["symptoms"]
    assert ctx["duration"] == "3 days"


def test_does_not_treat_lowercase_words_as_names(extractor):
    ctx = extractor.extract("I am having a headache", "en")
    assert "patient_name" not in ctx
    assert ctx["symptoms"] == ["headache"]


def test_extracts_requested_doctor(extractor):
    assert extractor.extract("Can I see Dr. Sharma tomorrow", "en")["requested_doctor"] == "Sharma"
    assert "requested_doctor" not in extractor.extract("I want to see the doctor", "en")


def test_extracts_hindi_context(extractor):
    ctx = extractor.extract("मेरा नाम राहुल है, मुझे बुखार है", "hi")
    assert ctx["patient_name"] == "राहुल"
    assert "fever" in ctx["symptoms"]
    assert ctx["preferred_language"] == "hi"


def test_merges_with_existing_context(extractor):
    ctx = extractor.extract("I also have a cough", "en", {"symptoms": ["fever"], "patient_name": "Asha"})
    assert sorted(ctx["symptoms"]) == ["cough", "fever"]
    assert ctx["patient_name"] == "Asha"


@pytest.fixture(params=["memory", "redis"])
def manager(request, offline_store, redis_store):
    return ConversationManager(offline_store if request.param == "memory" else redis_store)


def test_session_lifecycle(manager):
    sid = manager.create_session()
    assert manager.get_session(sid)["turn_count"] == 0

    manager.add_turn(sid, "My name is Asha and I have a fever", "Please consult a doctor.", "en")
    manager.add_turn(sid, "I also have a cough", "Noted.", "en")

    session = manager.get_session(sid)
    assert session["turn_count"] == 2
    assert session["patient_name"] == "Asha"
    assert len(session["history"]) == 4

    ctx = manager.get_conversation_context(sid)
    assert "Name: Asha" in ctx["context_string"]
    assert "Patient: I also have a cough" in ctx["context_string"]

    assert manager.clear_session(sid)
    assert manager.get_session(sid) is None


def test_add_turn_recreates_missing_session_with_same_id(manager):
    manager.add_turn("abc12345", "hello", "hi", "en")
    assert manager.get_session("abc12345")["turn_count"] == 1


def test_history_is_trimmed(manager):
    sid = manager.create_session()
    for i in range(ConversationManager.MAX_HISTORY_TURNS + 5):
        manager.add_turn(sid, f"question {i}", f"answer {i}", "en")
    history = manager.get_session(sid)["history"]
    assert len(history) == ConversationManager.MAX_HISTORY_TURNS * 2
    assert history[-1]["text"] == f"answer {ConversationManager.MAX_HISTORY_TURNS + 4}"


def test_repeated_query_count(manager):
    sid = manager.create_session()
    for _ in range(2):
        manager.add_turn(sid, "where is the pharmacy", "Ground floor.", "en")
    assert manager.get_repeated_query_count(sid, "Where is the pharmacy") == 2
    assert manager.get_repeated_query_count(sid, "what are the visiting hours") == 0
    assert manager.get_repeated_query_count("missing", "anything") == 0


def test_context_for_unknown_session(manager):
    assert manager.get_conversation_context("nope")["context_string"].startswith("New conversation")
