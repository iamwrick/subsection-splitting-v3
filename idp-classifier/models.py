import sys
from pydantic import BaseModel, field_validator

VALID_DOCUMENT_TYPES = {
    "Logbook Entry",
    "Authorized Release Certificate",
    "Work Card",
    "Certificate of Aircraft Registration",
    "Standard Airworthiness Certificate",
    "Major Repair and Alteration",
    "Statement of Compliance",
    "Supplemental Type Certificate",
    "Airworthiness Directive",
    "Service Bulletin",
    "Aircraft Flight Manual Supplement",
    "Instructions for Continued Airworthiness",
    "Status Report",
    "Other",
}


class Alternative(BaseModel):
    document_type: str
    confidence: float

    @field_validator("document_type")
    @classmethod
    def coerce_type(cls, v: str) -> str:
        if v not in VALID_DOCUMENT_TYPES:
            print(f"Warning: unknown alternative type '{v}' → Other", file=sys.stderr)
            return "Other"
        return v


class Zone(BaseModel):
    document_type: str
    confidence: float
    alternatives: list[Alternative] = []
    start_page: int
    end_page: int

    @field_validator("document_type")
    @classmethod
    def coerce_type(cls, v: str) -> str:
        if v not in VALID_DOCUMENT_TYPES:
            print(f"Warning: unknown document_type '{v}' → Other", file=sys.stderr)
            return "Other"
        return v


class ClassificationResult(BaseModel):
    zones: list[Zone]


class ChunkResult(BaseModel):
    chunk_index: int
    start_page: int
    end_page: int
    zones: list[Zone]


class TokenUsage(BaseModel):
    input_tokens: int = 0        # standard (non-cached) input tokens
    output_tokens: int = 0
    cache_write_tokens: int = 0  # tokens written to cache (1.25× price)
    cache_read_tokens: int = 0   # tokens read from cache  (0.10× price)

    def add(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
        )

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_write_tokens + self.cache_read_tokens

    def cost_usd(self,
                 price_input_per_1m: float  = 0.30,
                 price_output_per_1m: float = 2.50,
                 price_cache_write: float   = 0.375,
                 price_cache_read: float    = 0.030) -> float:
        return (
            self.input_tokens        / 1_000_000 * price_input_per_1m
          + self.output_tokens       / 1_000_000 * price_output_per_1m
          + self.cache_write_tokens  / 1_000_000 * price_cache_write
          + self.cache_read_tokens   / 1_000_000 * price_cache_read
        )


class DocumentResult(BaseModel):
    input_file: str
    total_pages: int
    total_chunks: int
    zones: list[Zone]
    token_usage: TokenUsage = TokenUsage()
