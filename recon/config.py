import json
import os
import subprocess
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()


def _live_foundry_urls() -> list[str] | None:
    """URLs the running Foundry Local server reports, or None if the CLI is unavailable/unparseable."""
    try:
        proc = subprocess.run(["foundry", "server", "status", "--output", "json"],
                              capture_output=True, text=True, timeout=30)
        out = proc.stdout + proc.stderr
        return list(json.loads(out[out.index("{"):]).get("webUrls") or []) or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


@dataclass
class Settings:
    # Set by foundry_preflight.py; no defaults so a run never starts against an unverified host/model.
    foundry_host: str = field(default_factory=lambda: os.getenv("FOUNDRY_HOST", "").rstrip("/"))
    model: str = field(default_factory=lambda: os.getenv("FOUNDRY_MODEL", ""))
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

    @property
    def openai_base_url(self) -> str:
        return f"{self.foundry_host}/v1"

    def require_model_env(self) -> None:
        missing = [n for n, v in [("FOUNDRY_HOST", self.foundry_host), ("FOUNDRY_MODEL", self.model)] if not v]
        if missing:
            raise RuntimeError(
                f"Missing {', '.join(missing)}. Run `python foundry_preflight.py --run` "
                "(it verifies the server/model, exports both variables, then starts the process)."
            )

    def check_server(self) -> str:
        """Verify the configured server is up, serves the model, and (when the foundry CLI is available) that
        the configured port is the one the running server reports. Raises RuntimeError on any failure."""
        self.require_model_env()
        redo = "Re-run `python foundry_preflight.py --run` to refresh FOUNDRY_HOST/FOUNDRY_MODEL."
        port = urlparse(self.foundry_host).port
        if not port:
            raise RuntimeError(f"FOUNDRY_HOST '{self.foundry_host}' has no explicit port. {redo}")

        live = _live_foundry_urls()
        if live is not None:
            live_ports = {urlparse(u).port for u in live}
            if port not in live_ports:
                raise RuntimeError(
                    f"Port mismatch: FOUNDRY_HOST uses {port} but the running server reports "
                    f"{', '.join(str(p) for p in sorted(p for p in live_ports if p))}. {redo}"
                )

        try:
            r = httpx.get(f"{self.openai_base_url}/models", timeout=15)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(f"Server at {self.foundry_host} is not reachable: {e}. {redo}") from e
        ids = [m.get("id") for m in r.json().get("data", [])]
        if self.model not in ids:
            raise RuntimeError(f"Model '{self.model}' is not served at {self.foundry_host} (served: {ids}). {redo}")
        return f"server up at {self.foundry_host} (port {port}), model {self.model} available"

    @property
    def statements_dir(self) -> str:
        return os.path.join(self.docs_folder, "statements")

    @property
    def client_trades_dir(self) -> str:
        return os.path.join(self.docs_folder, "client_trades")

    @property
    def master_dir(self) -> str:
        return os.path.join(self.docs_folder, "master")
