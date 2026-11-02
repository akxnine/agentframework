import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-opus-5-5"


@dataclass
class Settings:
    # Credentials come from ANTHROPIC_API_KEY (or an `ant auth login` profile); the SDK resolves them itself.
    model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", DEFAULT_MODEL))
    effort: str = field(default_factory=lambda: os.getenv("CLAUDE_EFFORT", "medium"))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("CLAUDE_MAX_TOKENS", "16000")))
    docs_folder: str = field(default_factory=lambda: os.getenv("DOCS_FOLDER", "./samples"))
    in_place: bool = False
    output_folder: str = field(default_factory=lambda: os.getenv("OUTPUT_FOLDER", "./output"))
    tenant_id: str = field(default_factory=lambda: os.getenv("GRAPH_TENANT_ID", ""))
    client_id: str = field(default_factory=lambda: os.getenv("GRAPH_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("GRAPH_CLIENT_SECRET", ""))
    sender: str = field(default_factory=lambda: os.getenv("GRAPH_SENDER", ""))
    email_to: list[str] = field(
        default_factory=lambda: [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]
    )

    def check_api(self) -> str:
        """Verify Claude credentials and model access with a free token-count call. Raises RuntimeError on failure."""
        import anthropic

        try:
            n = anthropic.Anthropic().messages.count_tokens(
                model=self.model, messages=[{"role": "user", "content": "ping"}]).input_tokens
        except (anthropic.AuthenticationError, TypeError) as e:  # TypeError: SDK found no credentials at all
            raise RuntimeError("Claude API authentication failed. Set ANTHROPIC_API_KEY (or run `ant auth login`).") from e
        except anthropic.NotFoundError as e:
            raise RuntimeError(f"Model '{self.model}' not found. Set CLAUDE_MODEL to a valid model id.") from e
        except anthropic.APIError as e:
            raise RuntimeError(f"Claude API check failed: {e}") from e
        return f"Claude API reachable, model {self.model} available ({n} test tokens)"

    @property
    def statements_dir(self) -> str:
        return os.path.join(self.docs_folder, "statements")

    @property
    def client_trades_dir(self) -> str:
        return os.path.join(self.docs_folder, "client_trades")

    @property
    def master_dir(self) -> str:
        return os.path.join(self.docs_folder, "master")
