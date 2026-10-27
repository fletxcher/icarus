"""Languages the assistant can be asked to reply in."""

from typing import Literal

# Code -> the language's name in English, as the model is told it.
LANGUAGES = {
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "tr": "Turkish",
    "ar": "Arabic",
    "hi": "Hindi",
    "zh": "Chinese (Simplified)",
    "ja": "Japanese",
    "ko": "Korean",
}

Language = Literal["en", "es", "fr", "de", "it", "pt", "ru", "tr", "ar", "hi", "zh", "ja", "ko"]
