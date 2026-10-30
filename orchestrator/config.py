import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    # Claude / Agent SDK
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"))
    claude_cli_path: str = field(default_factory=lambda: os.getenv("CLAUDE_CODE_CLI_PATH", ""))

    # Document locations
    docs_folder: str = field(default_factory=lambda: os.getenv("DOCS_FOLDER", "./samples"))
    in_place: bool = False
    output_folder: str = field(default_factory=lambda: os.getenv("OUTPUT_FOLDER", "./output"))

    # Microsoft Graph (compliance email: send report, poll for approve/reject replies)
    tenant_id: str = field(default_factory=lambda: os.getenv("GRAPH_TENANT_ID", ""))
    client_id: str = field(default_factory=lambda: os.getenv("GRAPH_CLIENT_ID", ""))
    client_secret: str = field(default_factory=lambda: os.getenv("GRAPH_CLIENT_SECRET", ""))
    sender: str = field(default_factory=lambda: os.getenv("GRAPH_SENDER", ""))
    email_to: list[str] = field(
        default_factory=lambda: [e.strip() for e in os.getenv("EMAIL_TO", "").split(",") if e.strip()]
    )

    # Salesforce (client<->advisor notes/comments)
    sf_domain: str = field(default_factory=lambda: os.getenv("SF_DOMAIN", ""))  # e.g. https://mycompany.my.salesforce.com
    sf_client_id: str = field(default_factory=lambda: os.getenv("SF_CLIENT_ID", ""))
    sf_client_secret: str = field(default_factory=lambda: os.getenv("SF_CLIENT_SECRET", ""))
    sf_username: str = field(default_factory=lambda: os.getenv("SF_USERNAME", ""))
    sf_password: str = field(default_factory=lambda: os.getenv("SF_PASSWORD", ""))
    sf_security_token: str = field(default_factory=lambda: os.getenv("SF_SECURITY_TOKEN", ""))

    # Compliance ledger / watcher
    ledger_path: str = field(default_factory=lambda: os.getenv("LEDGER_PATH", "./output/compliance_ledger.sqlite3"))
    compliance_poll_seconds: int = field(default_factory=lambda: int(os.getenv("COMPLIANCE_POLL_SECONDS", "60")))

    def require_anthropic_env(self) -> None:
        if not self.anthropic_api_key:
            raise RuntimeError(
                "Missing ANTHROPIC_API_KEY. Set it in the environment or a local .env file; see "
                "docs/DEPLOYMENT.md for Claude Agent SDK setup (API key or a Bedrock/Vertex Claude Code profile)."
            )

    def require_email_env(self) -> list[str]:
        return [n for n, v in [("GRAPH_TENANT_ID", self.tenant_id), ("GRAPH_CLIENT_ID", self.client_id),
                               ("GRAPH_CLIENT_SECRET", self.client_secret), ("GRAPH_SENDER", self.sender),
                               ("EMAIL_TO", self.email_to)] if not v]

    def require_salesforce_env(self) -> list[str]:
        return [n for n, v in [("SF_DOMAIN", self.sf_domain), ("SF_CLIENT_ID", self.sf_client_id),
                               ("SF_CLIENT_SECRET", self.sf_client_secret)] if not v]

    @property
    def statements_dir(self) -> str:
        return os.path.join(self.docs_folder, "statements")

    @property
    def client_trades_dir(self) -> str:
        return os.path.join(self.docs_folder, "client_trades")

    @property
    def master_dir(self) -> str:
        return os.path.join(self.docs_folder, "master")
