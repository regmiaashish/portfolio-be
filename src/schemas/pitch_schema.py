from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_INPUT_LENGTH = 80
# Mirrors the frontend rule /^[\p{L}\p{N} .,&'()\/+#-]{1,80}$/u. No < > or newlines, so input
# can't break out of the prompt's delimiter tags.
ALLOWED_PUNCTUATION = frozenset(" .,&'()/+#-")


class PitchQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    company: str = Field(..., min_length=1, max_length=MAX_INPUT_LENGTH, examples=["Spotify"])
    role: str = Field(..., min_length=1, max_length=MAX_INPUT_LENGTH, examples=["Backend engineer"])
    turnstile_token: str | None = Field(default=None, max_length=2048)

    @field_validator("company", "role")
    @classmethod
    def allowed_characters(cls, value: str) -> str:
        if not all(ch.isalnum() or ch in ALLOWED_PUNCTUATION for ch in value):
            raise ValueError("Use letters, numbers, spaces and . , & ' ( ) / + # - only.")
        return value
