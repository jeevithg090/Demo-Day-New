"""Pure, model-free text analysis used by the voice pipeline.

This module holds the "Layer-1" logic that runs between speech-to-text and the
LLM: script-based language detection, intent/urgency/stress signal extraction,
the safety gate that decides when to escalate to a human, the policy engine and
prompt construction. Nothing here touches the network or loads ML models, which
keeps it fast and easy to unit test.
"""

from __future__ import annotations

import re
import time
from typing import Any

SUPPORTED_LANGUAGES: dict[str, str] = {
    "kn": "Kannada",
    "ta": "Tamil",
    "te": "Telugu",
    "hi": "Hindi",
    "en": "English",
}

# Unicode script ranges for language detection
SCRIPT_RANGES: dict[str, list[tuple[int, int]]] = {
    "kn": [(0x0C80, 0x0CFF)],  # Kannada
    "ta": [(0x0B80, 0x0BFF)],  # Tamil
    "te": [(0x0C00, 0x0C7F)],  # Telugu
    "hi": [(0x0900, 0x097F)],  # Devanagari
    "en": [(0x0041, 0x005A), (0x0061, 0x007A)],  # Latin A-Z, a-z
}

# Multi-language keyword dictionaries for Layer-1 signals
INTENT_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "medical_query": {
        "en": [
            "pain",
            "fever",
            "headache",
            "cough",
            "cold",
            "stomach",
            "doctor",
            "medicine",
            "treatment",
            "symptom",
            "sick",
            "ill",
            "hurt",
            "ache",
        ],
        "hi": ["दर्द", "बुखार", "सिरदर्द", "खांसी", "सर्दी", "पेट", "डॉक्टर", "दवाई", "इलाज", "बीमार"],
        "ta": ["வலி", "காய்ச்சல்", "தலைவலி", "இருமல்", "சளி", "வயிறு", "மருத்துவர்", "மருந்து"],
        "te": ["నొప్పి", "జ్వరం", "తలనొప్పి", "దగ్గు", "జలుబు", "కడుపు", "డాక్టర్", "మందు"],
        "kn": ["ನೋವು", "ಜ್ವರ", "ತಲೆನೋವು", "ಕೆಮ್ಮು", "ಶೀತ", "ಹೊಟ್ಟೆ", "ವೈದ್ಯರು", "ಔಷಧಿ"],
    },
    "appointment": {
        "en": [
            "appointment",
            "book",
            "schedule",
            "slot",
            "available",
            "timing",
            "visit",
            "meet doctor",
            "see doctor",
        ],
        "hi": ["अपॉइंटमेंट", "बुक", "समय", "मिलना", "डॉक्टर से मिलना"],
        "ta": ["நேரம்", "முன்பதிவு", "சந்திப்பு"],
        "te": ["అపాయింట్మెంట్", "బుక్", "సమయం"],
        "kn": ["ಅಪಾಯಿಂಟ್ಮೆಂಟ್", "ಬುಕ್", "ಸಮಯ", "ಭೇಟಿ"],
    },
    "greeting": {
        "en": ["hello", "hi", "hey", "good morning", "good evening", "namaste"],
        "hi": ["नमस्ते", "नमस्कार", "हैलो"],
        "ta": ["வணக்கம்", "ஹலோ"],
        "te": ["నమస్కారం", "హలో"],
        "kn": ["ನಮಸ್ಕಾರ", "ಹಲೋ"],
    },
    "admin": {
        "en": [
            "bill",
            "payment",
            "insurance",
            "cost",
            "price",
            "charge",
            "report",
            "record",
            "visiting hours",
            "parking",
        ],
        "hi": ["बिल", "पेमेंट", "बीमा", "रिपोर्ट"],
        "ta": ["பில்", "கட்டணம்", "காப்பீடு"],
        "te": ["బిల్", "చెల్లింపు", "బీమా"],
        "kn": ["ಬಿಲ್", "ಪಾವತಿ", "ವಿಮೆ"],
    },
}

# Urgency keywords - HIGH priority
URGENCY_HIGH: dict[str, list[str]] = {
    "en": [
        "severe",
        "extreme",
        "unbearable",
        "emergency",
        "urgent",
        "immediately",
        "can't breathe",
        "chest pain",
        "unconscious",
        "bleeding",
        "accident",
        "critical",
        "dying",
        "collapsed",
    ],
    "hi": ["गंभीर", "बहुत", "असहनीय", "इमरजेंसी", "तुरंत", "सांस नहीं", "छाती में दर्द", "बेहोश", "खून"],
    "ta": ["கடுமையான", "அவசரம்", "உடனடி", "மூச்சு", "நெஞ்சு வலி", "மயக்கம்", "இரத்தம்"],
    "te": ["తీవ్రమైన", "అత్యవసరం", "వెంటనే", "ఊపిరి", "ఛాతీ నొప్పి", "స్పృహ", "రక్తం"],
    "kn": ["ತೀವ್ರ", "ತುರ್ತು", "ತಕ್ಷಣ", "ಉಸಿರು", "ಎದೆ ನೋವು", "ಪ್ರಜ್ಞೆ", "ರಕ್ತ"],
}

# Critical symptoms that always require escalation
CRITICAL_SYMPTOMS: dict[str, list[str]] = {
    "en": [
        "chest pain",
        "heart attack",
        "can't breathe",
        "difficulty breathing",
        "unconscious",
        "seizure",
        "stroke",
        "severe bleeding",
        "choking",
        "suicide",
        "poisoning",
        "overdose",
    ],
    "hi": ["छाती में दर्द", "हार्ट अटैक", "सांस नहीं", "बेहोश", "दौरा", "खून बह रहा", "जहर"],
    "ta": ["நெஞ்சு வலி", "மாரடைப்பு", "மூச்சு திணறல்", "மயக்கம்", "வலிப்பு"],
    "te": ["ఛాతీ నొప్పి", "గుండెపోటు", "ఊపిరి ఆడటం లేదు", "స్పృహ లేదు"],
    "kn": ["ಎದೆ ನೋವು", "ಹೃದಯಾಘಾತ", "ಉಸಿರಾಟ ತೊಂದರೆ", "ಪ್ರಜ್ಞೆ ತಪ್ಪು"],
}

# Stress indicators - emotional distress signals
STRESS_INDICATORS: dict[str, list[str]] = {
    "en": [
        "help me",
        "please help",
        "very worried",
        "scared",
        "afraid",
        "anxious",
        "can't sleep",
        "desperate",
        "terrible",
        "worst pain",
        "unbearable",
    ],
    "hi": ["मदद करो", "बहुत चिंता", "डर", "नींद नहीं", "असहनीय"],
    "ta": ["உதவி", "பயம்", "கவலை", "தூக்கமின்மை"],
    "te": ["సహాయం", "భయం", "ఆందోళన"],
    "kn": ["ಸಹಾಯ", "ಭಯ", "ಚಿಂತೆ", "ನಿದ್ರೆ ಬರುತ್ತಿಲ್ಲ"],
}

BREATHING_KEYWORDS = ["chest", "breathe", "breathing", "heart", "छाती", "सांस", "ಎದೆ", "ಉಸಿರು"]

DEFAULT_POLICY_CONFIG: dict[str, Any] = {
    "hospital_name": "Apollo Hospital",
    "emergency_number": "108",
    "helpline": "1860-500-1066",
    "tone": "formal",
    "max_words": 50,
    "disclaimer_required": True,
    "disclaimer_text": "This is general information only. Please consult a doctor for proper medical advice.",
    "always_recommend_doctor": True,
}

EMERGENCY_MAX_WORDS = 80
REPEAT_ESCALATION_THRESHOLD = 2

_LATIN_RE = re.compile(r"^[\x00-\x7f]+$")


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def contains_keyword(text_lower: str, keyword: str) -> bool:
    """Return True if `keyword` occurs in `text_lower`.

    ASCII keywords are matched on word boundaries so that, for example, "hi"
    does not match "this" and "ill" does not match "will". Indic-script
    keywords are matched as substrings because these languages attach
    suffixes directly to words.
    """
    kw = keyword.lower()
    if _LATIN_RE.match(kw):
        return re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", text_lower) is not None
    return kw in text_lower


def _keywords_for(table: dict[str, list[str]], language: str) -> list[str]:
    keywords = list(table.get(language, []))
    if language != "en":
        keywords += table.get("en", [])
    return keywords


def detect_script_language(text: str) -> dict[str, Any]:
    """Detect the language of `text` from the Unicode script of its characters."""
    start = time.perf_counter()
    char_counts = {lang: 0 for lang in SCRIPT_RANGES}

    for char in text:
        code_point = ord(char)
        for lang, ranges in SCRIPT_RANGES.items():
            if any(lo <= code_point <= hi for lo, hi in ranges):
                char_counts[lang] += 1
                break

    total = sum(char_counts.values())
    if total == 0:
        detected, confidence = "en", 0.5
    else:
        detected = max(char_counts, key=lambda lang: char_counts[lang])
        confidence = char_counts[detected] / total

    return {
        "language": detected,
        "language_name": SUPPORTED_LANGUAGES.get(detected, "Unknown"),
        "confidence": round(confidence, 3),
        "char_distribution": char_counts,
        "latency_ms": _elapsed_ms(start),
    }


def extract_layer1_signals(text: str, language: str) -> dict[str, Any]:
    """Extract intent, urgency, tone and stress signals from a transcript."""
    start = time.perf_counter()
    text_lower = text.lower()

    intent = "general"
    intent_confidence = 0.0
    for intent_type, lang_keywords in INTENT_KEYWORDS.items():
        keywords = _keywords_for(lang_keywords, language)
        matches = sum(1 for kw in keywords if contains_keyword(text_lower, kw))
        if matches:
            confidence = min(matches / 3, 1.0)
            if confidence > intent_confidence:
                intent, intent_confidence = intent_type, confidence
    if intent == "general":
        intent_confidence = 0.5

    urgency_count = sum(1 for kw in _keywords_for(URGENCY_HIGH, language) if contains_keyword(text_lower, kw))
    if urgency_count >= 2:
        urgency = "high"
    elif urgency_count == 1:
        urgency = "medium"
    else:
        urgency = "normal"

    stressed = any(contains_keyword(text_lower, kw) for kw in _keywords_for(STRESS_INDICATORS, language))
    stress_level = "stressed" if stressed else "calm"
    tone = "stressed" if urgency == "high" or stressed or "!" in text else "calm"

    return {
        "intent": intent,
        "intent_confidence": round(intent_confidence, 3),
        "urgency": urgency,
        "urgency_count": urgency_count,
        "tone": tone,
        "stress_level": stress_level,
        "latency_ms": _elapsed_ms(start),
    }


def run_safety_gate(
    text: str,
    language: str,
    signals: dict[str, Any],
    repeat_count: int = 0,
    custom_keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Decide whether a query must be escalated to a human.

    Rules:
        1. Critical symptom (or an admin-configured escalation keyword) -> always escalate
        2. Chest/breathing concern on a medical query -> escalate
        3. Low-confidence medical query -> escalate
        4. Two or more urgency keywords -> escalate
        5. The same question asked repeatedly in the session -> escalate
    """
    start = time.perf_counter()
    text_lower = text.lower()
    reasons: list[str] = []
    rules: list[str] = []

    for symptom in _keywords_for(CRITICAL_SYMPTOMS, language):
        if contains_keyword(text_lower, symptom):
            reasons.append(f"Critical symptom detected: {symptom}")
            rules.append("rule_1_critical_symptom")
            break
    else:
        for keyword in custom_keywords or []:
            if keyword.strip() and contains_keyword(text_lower, keyword.strip()):
                reasons.append(f"Escalation keyword detected: {keyword.strip()}")
                rules.append("rule_1_critical_symptom")
                break

    if signals["intent"] == "medical_query" and any(
        contains_keyword(text_lower, kw) for kw in BREATHING_KEYWORDS
    ):
        reasons.append("Chest/breathing concern with medical query")
        rules.append("rule_2_breathing_medical")

    if signals["intent"] == "medical_query" and signals["intent_confidence"] < 0.3:
        reasons.append("Low confidence medical query - needs clarification")
        rules.append("rule_3_low_confidence")

    if signals.get("urgency_count", 0) >= 2:
        reasons.append("Multiple urgency indicators detected")
        rules.append("rule_4_high_urgency")

    if repeat_count >= REPEAT_ESCALATION_THRESHOLD:
        reasons.append(f"Repeated query ({repeat_count}x) - patient may need direct assistance")
        rules.append("rule_5_repeated_query")

    return {
        "should_escalate": bool(rules),
        "escalation_reason": reasons[0] if reasons else None,
        "rules_triggered": rules,
        "latency_ms": _elapsed_ms(start),
    }


def apply_policy(
    safety: dict[str, Any], language: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Merge the admin-configured hospital policy with per-query safety signals."""
    start = time.perf_counter()
    cfg = {**DEFAULT_POLICY_CONFIG, **(config or {})}

    policy: dict[str, Any] = {
        "hospital_name": cfg["hospital_name"],
        "response_language": language,
        "max_words": int(cfg["max_words"]),
        "tone": cfg["tone"],
        "include_doctor_recommendation": cfg["always_recommend_doctor"],
        "emergency_number": cfg["emergency_number"],
        "helpline": cfg["helpline"],
        "disclaimer": cfg["disclaimer_required"],
        "disclaimer_text": cfg["disclaimer_text"],
    }

    if safety.get("should_escalate"):
        policy["priority"] = "urgent"
        policy["include_emergency_info"] = True
        policy["max_words"] = max(policy["max_words"], EMERGENCY_MAX_WORDS)
    else:
        policy["priority"] = "normal"
        policy["include_emergency_info"] = False

    policy["latency_ms"] = _elapsed_ms(start)
    return policy


def build_system_prompt(
    language: str,
    signals: dict[str, Any],
    safety: dict[str, Any],
    policy: dict[str, Any],
    context_string: str = "",
    rag_context: str = "",
) -> str:
    """System instruction for the single-shot `/process` endpoint."""
    lang_name = SUPPORTED_LANGUAGES.get(language, "English")
    urgent = (
        "URGENT: This may be an emergency situation. Prioritize safety and recommend "
        "immediate medical attention."
        if safety.get("should_escalate")
        else ""
    )
    disclaimer = policy.get("disclaimer_text", "") if policy.get("disclaimer", True) else ""
    return f"""You are a helpful medical assistant for {policy["hospital_name"]}.
Follow these rules strictly:
1. Respond in {lang_name} language using the native script
2. Keep responses under {policy["max_words"]} words
3. Use a {policy["tone"]} tone
4. Always recommend consulting a doctor for medical issues
5. For emergencies, mention calling {policy["emergency_number"]}
6. Never diagnose or prescribe medication
7. Be empathetic and professional
8. Helpline: {policy["helpline"]}

Patient intent: {signals["intent"]}
Urgency level: {signals["urgency"]}
{urgent}

Additional Context:
{context_string}

Relevant Information:
{rag_context}

Disclaimer: {disclaimer}
"""


def build_call_system_prompt(
    language: str,
    signals: dict[str, Any],
    safety: dict[str, Any],
    policy: dict[str, Any],
    context_string: str = "",
) -> str:
    """System instruction for the low-latency live call (WebSocket) mode."""
    lang_name = SUPPORTED_LANGUAGES.get(language, "English")
    urgent = (
        f"\nURGENT: This may be an emergency. Tell the caller to call {policy['emergency_number']} now."
        if safety.get("should_escalate")
        else ""
    )
    return f"""You are a helpful medical assistant for {policy["hospital_name"]}.
Follow these rules strictly:
1. Respond in {lang_name} language.
2. Keep responses conversational and brief (1-2 sentences).
3. Be empathetic and professional. Never diagnose or prescribe medication.
4. Use the provided context to answer personal questions (name, symptoms, etc).{urgent}

Context from previous turns:
{context_string}

Intent: {signals["intent"]}
"""


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!।])\s+")


def split_sentences(text: str) -> list[str]:
    """Split a response into sentences for incremental TTS (handles the Devanagari danda)."""
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
