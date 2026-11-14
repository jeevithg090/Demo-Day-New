import pytest

from voice_ai.signals import (
    apply_policy,
    build_call_system_prompt,
    build_system_prompt,
    contains_keyword,
    detect_script_language,
    extract_layer1_signals,
    run_safety_gate,
    split_sentences,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I have a headache", "en"),
        ("मुझे बुखार है", "hi"),
        ("ನನಗೆ ಜ್ವರ ಇದೆ", "kn"),
        ("எனக்கு காய்ச்சல்", "ta"),
        ("నాకు జ్వరం ఉంది", "te"),
    ],
)
def test_detect_script_language(text, expected):
    result = detect_script_language(text)
    assert result["language"] == expected
    assert result["confidence"] > 0.5


def test_detect_script_language_defaults_to_english_without_letters():
    result = detect_script_language("123 ???")
    assert result["language"] == "en"
    assert result["confidence"] == 0.5


def test_contains_keyword_uses_word_boundaries_for_latin_text():
    assert contains_keyword("hi there", "hi")
    assert not contains_keyword("this is it", "hi")
    assert not contains_keyword("i will come", "ill")
    assert contains_keyword("i can't breathe", "can't breathe")


def test_contains_keyword_substring_for_indic_scripts():
    # Suffixes attach directly to the word in Indic languages
    assert contains_keyword("ತಲೆನೋವಿದೆ ಮತ್ತು ಜ್ವರವಿದೆ", "ಜ್ವರ")


def test_single_symptom_is_a_medical_query():
    signals = extract_layer1_signals("I have a fever", "en")
    assert signals["intent"] == "medical_query"
    assert signals["urgency"] == "normal"
    assert signals["tone"] == "calm"


def test_greeting_not_triggered_by_substring():
    signals = extract_layer1_signals("this is about my bill payment", "en")
    assert signals["intent"] == "admin"


def test_general_intent_when_no_keywords():
    signals = extract_layer1_signals("what is the weather", "en")
    assert signals["intent"] == "general"
    assert signals["intent_confidence"] == 0.5


def test_urgency_and_stress_levels():
    signals = extract_layer1_signals("Severe bleeding after an accident, please help!", "en")
    assert signals["urgency"] == "high"
    assert signals["urgency_count"] >= 2
    assert signals["stress_level"] == "stressed"
    assert signals["tone"] == "stressed"


def test_regional_keywords_fall_back_to_english():
    signals = extract_layer1_signals("मुझे fever है", "hi")
    assert signals["intent"] == "medical_query"


def test_safety_gate_escalates_critical_symptom():
    text = "I have chest pain"
    signals = extract_layer1_signals(text, "en")
    safety = run_safety_gate(text, "en", signals)
    assert safety["should_escalate"]
    assert "rule_1_critical_symptom" in safety["rules_triggered"]
    assert safety["escalation_reason"].startswith("Critical symptom")


def test_safety_gate_escalates_critical_symptom_in_kannada():
    text = "ನನಗೆ ಎದೆ ನೋವು ಇದೆ"
    signals = extract_layer1_signals(text, "kn")
    safety = run_safety_gate(text, "kn", signals)
    assert safety["should_escalate"]


def test_safety_gate_breathing_with_medical_query():
    text = "my breathing is bad and I have a cough"
    signals = extract_layer1_signals(text, "en")
    safety = run_safety_gate(text, "en", signals)
    assert "rule_2_breathing_medical" in safety["rules_triggered"]


def test_safety_gate_repeated_query():
    text = "where is the pharmacy"
    signals = extract_layer1_signals(text, "en")
    assert not run_safety_gate(text, "en", signals, repeat_count=1)["should_escalate"]
    safety = run_safety_gate(text, "en", signals, repeat_count=2)
    assert safety["rules_triggered"] == ["rule_5_repeated_query"]


def test_safety_gate_no_escalation_for_routine_question():
    text = "What are the visiting hours?"
    signals = extract_layer1_signals(text, "en")
    safety = run_safety_gate(text, "en", signals)
    assert safety == {**safety, "should_escalate": False, "escalation_reason": None, "rules_triggered": []}


def test_apply_policy_uses_config_and_defaults():
    policy = apply_policy(
        {"should_escalate": False}, "hi", {"hospital_name": "Test Hospital", "max_words": 30}
    )
    assert policy["hospital_name"] == "Test Hospital"
    assert policy["max_words"] == 30
    assert policy["emergency_number"] == "108"
    assert policy["priority"] == "normal"
    assert policy["response_language"] == "hi"


def test_apply_policy_emergency_allows_longer_answers():
    policy = apply_policy({"should_escalate": True}, "en", {"max_words": 30})
    assert policy["priority"] == "urgent"
    assert policy["include_emergency_info"] is True
    assert policy["max_words"] == 80


def test_system_prompts_include_policy_and_context():
    signals = extract_layer1_signals("chest pain", "en")
    safety = run_safety_gate("chest pain", "en", signals)
    policy = apply_policy(safety, "ta", {"hospital_name": "Test Hospital"})
    prompt = build_system_prompt("ta", signals, safety, policy, "PATIENT INFORMATION", "FAQ 1")
    assert "Test Hospital" in prompt
    assert "Tamil" in prompt
    assert "URGENT" in prompt
    assert "PATIENT INFORMATION" in prompt and "FAQ 1" in prompt

    call_prompt = build_call_system_prompt("ta", signals, safety, policy, "ctx")
    assert "Test Hospital" in call_prompt and "call 108" in call_prompt


def test_split_sentences_handles_danda_and_latin_punctuation():
    assert split_sentences("Hello. How are you? Fine!") == ["Hello.", "How are you?", "Fine!"]
    assert split_sentences("नमस्ते। आप कैसे हैं?") == ["नमस्ते।", "आप कैसे हैं?"]
    assert split_sentences("   ") == []


def test_safety_gate_admin_configured_keywords():
    text = "I think I swallowed a battery"
    signals = extract_layer1_signals(text, "en")
    assert not run_safety_gate(text, "en", signals)["should_escalate"]
    safety = run_safety_gate(text, "en", signals, custom_keywords=["swallowed", " "])
    assert safety["should_escalate"]
    assert safety["escalation_reason"] == "Escalation keyword detected: swallowed"
