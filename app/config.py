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
    SECRET_KEY = os.environ.get("SECRET_KEY")
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "uploads")

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
    EMBEDDING_URL = os.environ.get("EMBEDDING_URL")
    EMBEDDING_KEY = os.environ.get("EMBEDDING_KEY")
    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL")
    EMBEDDING_DIM = _int_env("EMBEDDING_DIM")   # MUST be set — single source of truth

    # --- Conversation / QA gateway (OpenAI chat.completions compatible) ---
    QA_URL = os.environ.get("QA_URL")
    QA_KEY = os.environ.get("QA_KEY")
    QA_MODEL = os.environ.get("QA_MODEL")

    # --- RRF ---
    RRF_K = _int_env("RRF_K", 60)
    VECTOR_SCORE_THRESHOLD = float(os.environ.get("VECTOR_SCORE_THRESHOLD", "0.35"))

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
        if not cls.SECRET_KEY:
            missing.append("SECRET_KEY")
        if not cls.EMBEDDING_URL:
            missing.append("EMBEDDING_URL")
        if not cls.EMBEDDING_KEY:
            missing.append("EMBEDDING_KEY")
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
