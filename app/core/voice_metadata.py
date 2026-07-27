"""Voice catalog for the Gemini Live `gemini-live-2.5-flash-native-audio` model.

Gemini Live exposes 30 prebuilt voice names — these are the only voices the
agent can speak with. The admin voice picker reads this list to populate the
selector when configuring an agent.

https://ai.google.dev/gemini-api/docs/live#voices
"""

from typing import Dict, List


def _voice(name: str, category: str, description: str) -> Dict[str, str]:
    return {
        "voice_id": name,
        "name": name,
        "description": description,
        "category": category,
        "use_case": "general",
    }


VOICE_METADATA: List[Dict[str, str]] = [
    _voice("Zephyr", "female", "Bright"),
    _voice("Puck", "male", "Upbeat"),
    _voice("Charon", "male", "Informative"),
    _voice("Kore", "female", "Firm"),
    _voice("Fenrir", "male", "Excitable"),
    _voice("Leda", "female", "Youthful"),
    _voice("Orus", "male", "Firm"),
    _voice("Aoede", "female", "Breezy"),
    _voice("Callirrhoe", "female", "Easy-going"),
    _voice("Autonoe", "female", "Bright"),
    _voice("Enceladus", "male", "Breathy"),
    _voice("Iapetus", "male", "Clear"),
    _voice("Umbriel", "male", "Easy-going"),
    _voice("Algieba", "male", "Smooth"),
    _voice("Despina", "female", "Smooth"),
    _voice("Erinome", "female", "Clear"),
    _voice("Algenib", "male", "Gravelly"),
    _voice("Rasalgethi", "male", "Informative"),
    _voice("Laomedeia", "female", "Upbeat"),
    _voice("Achernar", "female", "Soft"),
    _voice("Alnilam", "male", "Firm"),
    _voice("Schedar", "male", "Even"),
    _voice("Gacrux", "female", "Mature"),
    _voice("Pulcherrima", "female", "Forward"),
    _voice("Achird", "male", "Friendly"),
    _voice("Zubenelgenubi", "male", "Casual"),
    _voice("Vindemiatrix", "female", "Gentle"),
    _voice("Sadachbia", "male", "Lively"),
    _voice("Sadaltager", "male", "Knowledgeable"),
    _voice("Sulafat", "female", "Warm"),
]


def get_voice_by_id(voice_id: str) -> Dict[str, str] | None:
    if not voice_id:
        return None
    for voice in VOICE_METADATA:
        if voice["voice_id"] == voice_id:
            return voice
    return None


def get_all_voices() -> List[Dict[str, str]]:
    return list(VOICE_METADATA)
