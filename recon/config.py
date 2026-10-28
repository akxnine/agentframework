import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "gemma4:12b"))
    docs_folder: str = field(default_factory=lambda: os.getenv("DOCS_FOLDER", "./samples"))
    output_folder: str = field(default_factory=lambda: os.getenv("OUTPUT_FOLDER", "./output"))
    tenant_id: str = field(default_factory=lambda: os.getenv("GRAPH_TENANT_ID", ""))
    client_id: str = field(default_factory=lambda: os.getenv("GRAPH_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("GRAPH_CLIENT_SECRET", ""))
    sender: str = field(default_factory=lambda: os.getenv("GRAPH_SENDER", ""))
    email_to: list[str] = field(
        default_factory=lambda: [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]
    )
