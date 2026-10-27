"""Load configuration from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int | None = None) -> int | None:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        raise RuntimeError(f"{name} must be an integer, got: {v!r}")


class Config:
    # --- Flask ---
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")

    # --- JWT (no Flask session anywhere; identity travels in the token) ---
    JWT_SECRET = os.environ.get("JWT_SECRET")
    JWT_ALGORITHM = "HS256"
    JWT_TTL_HOURS = _int_env("JWT_TTL_HOURS", 24)

    # --- MySQL ---
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "mysql")
    MYSQL_PORT = _int_env("MYSQL_PORT", 3306)
    MYSQL_USER = os.environ.get("MYSQL_USER", "campus")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "campus")
    MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "campusclaw")

    # --- Qdrant ---
    QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333")
    QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "campusclaw_chunks")

    # --- Embedding gateway (OpenAI compatible /embeddings) ---
    # 变量名对齐课程网关页面：EMBEDDING_BASE_URL 只写到 /v1（代码接 /embeddings），
    # EMBEDDING_DIM 不是请求参数，用于建库与校验返回向量长度。
    EMBEDDING_URL = os.environ.get("EMBEDDING_BASE_URL")
    EMBEDDING_KEY = os.environ.get("EMBEDDING_API_KEY")
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL")
    EMBEDDING_DIM = _int_env("EMBEDDING_DIM")   # MUST be set — single source of truth

    # --- Conversation / QA gateway (OpenAI chat.completions compatible) ---
    QA_URL = os.environ.get("QA_URL")
    QA_KEY = os.environ.get("QA_KEY")
    QA_MODEL = os.environ.get("QA_MODEL")

    # --- Assistant chat gateway (lesson 5) — OpenAI chat.completions, streamed ---
    # CHAT_BASE_URL is the API ROOT (e.g. .../v1); the route is appended by
    # chat_endpoint(). Key falls back to QA_KEY so one credential covers both.
    CHAT_BASE_URL = os.environ.get(
        "CHAT_BASE_URL", "https://ai-gateway.devops.hello1023.com/v1")
    CHAT_API_KEY = os.environ.get("CHAT_API_KEY") or os.environ.get("QA_KEY")
    CHAT_MODEL = os.environ.get("CHAT_MODEL", "course-chat")

    # --- RRF ---
    RRF_K = _int_env("RRF_K", 60)
    VECTOR_SCORE_THRESHOLD = float(os.environ.get("VECTOR_SCORE_THRESHOLD", "0.35"))

    @classmethod
    def chat_endpoint(cls) -> str:
        """Full chat.completions URL built from CHAT_BASE_URL."""
        return cls.CHAT_BASE_URL.rstrip("/") + "/chat/completions"

    @classmethod
    def mysql_dsn(cls) -> dict:
        return dict(
            host=cls.MYSQL_HOST,
            port=cls.MYSQL_PORT,
            user=cls.MYSQL_USER,
            password=cls.MYSQL_PASSWORD,
            database=cls.MYSQL_DATABASE,
            charset="utf8mb4",
        )

    @classmethod
    def validate(cls):
        missing = []
        if not cls.JWT_SECRET:
            missing.append("JWT_SECRET")
        if not cls.EMBEDDING_URL:
            missing.append("EMBEDDING_BASE_URL")
        if not cls.EMBEDDING_KEY:
            missing.append("EMBEDDING_API_KEY")
        if not cls.EMBEDDING_MODEL:
            missing.append("EMBEDDING_MODEL")
        if cls.EMBEDDING_DIM is None:
            missing.append("EMBEDDING_DIM")
        if not cls.QA_URL:
            missing.append("QA_URL")
        if not cls.QA_KEY:
            missing.append("QA_KEY")
        if not cls.QA_MODEL:
            missing.append("QA_MODEL")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in."
            )
        return cls
