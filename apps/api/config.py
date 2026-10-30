import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./tracelens.db")
    checkpoint_path: str = os.getenv("CHECKPOINT_PATH", "")
    llm_mode: str = os.getenv("LLM_MODE", "mock")
    retrieval_mode: str = os.getenv("RETRIEVAL_MODE", "lexical")
    tool_transport: str = os.getenv("TOOL_TRANSPORT", "direct")
    demo_password: str = os.getenv("DEMO_PASSWORD", "tracelens-local")
    mcp_signing_secret: str = os.getenv("MCP_SIGNING_SECRET", "tracelens-local-scope-secret-v1")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    allowed_origins: tuple[str, ...] = tuple(
        os.getenv(
            "ALLOWED_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173",
        ).split(",")
    )
    # Live LLM provider configuration (server-side only; never exposed to browser)
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    llm_allow_degraded_fallback: bool = (
        os.getenv("LLM_ALLOW_DEGRADED_FALLBACK", "false").lower() == "true"
    )
    llm_input_price_per_1m: float | None = (
        float(os.environ["LLM_INPUT_PRICE_PER_1M"])
        if os.getenv("LLM_INPUT_PRICE_PER_1M")
        else None
    )
    llm_output_price_per_1m: float | None = (
        float(os.environ["LLM_OUTPUT_PRICE_PER_1M"])
        if os.getenv("LLM_OUTPUT_PRICE_PER_1M")
        else None
    )
    llm_pricing_as_of: str | None = os.getenv("LLM_PRICING_AS_OF") or None
    # Independent embedding provider configuration for hybrid retrieval
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", "")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "")
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", "https://api.openai.com/v1")
    # Workflow budgets
    max_tool_calls: int = int(os.getenv("MAX_TOOL_CALLS", "6"))
    max_llm_calls: int = int(os.getenv("MAX_LLM_CALLS", "3"))
    max_repairs: int = int(os.getenv("MAX_REPAIRS", "1"))
    max_token_budget: int = int(os.getenv("MAX_TOKEN_BUDGET", "16000"))
    wall_clock_timeout_seconds: int = int(os.getenv("WALL_CLOCK_TIMEOUT_SECONDS", "45"))
    # Evaluation artifact path
    benchmark_artifact_path: str = os.getenv(
        "BENCHMARK_ARTIFACT_PATH", "evaluation/results/mock_benchmark_summary.json"
    )


settings = Settings()
