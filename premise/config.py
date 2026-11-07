"""Runtime configuration from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    falkordb_host: str = field(default_factory=lambda: os.getenv("FALKORDB_HOST", "localhost"))
    falkordb_port: int = field(default_factory=lambda: int(os.getenv("FALKORDB_PORT", "6379")))
    falkordb_username: str = field(default_factory=lambda: os.getenv("FALKORDB_USERNAME", ""))
    falkordb_password: str = field(default_factory=lambda: os.getenv("FALKORDB_PASSWORD", ""), repr=False)
    falkordb_tls: bool = field(default_factory=lambda: os.getenv("FALKORDB_TLS", "false").lower() == "true")
    public_demo: bool = field(default_factory=lambda: os.getenv("PUBLIC_DEMO", "false").lower() == "true")
    graph_name: str = field(default_factory=lambda: os.getenv("GRAPH_NAME", "premise_kestrel"))
    sandbox_dir: str = field(default_factory=lambda: os.getenv("SANDBOX_DIR", "sandbox/kestrel-platform"))
    since_days: int = field(default_factory=lambda: int(os.getenv("SINCE_DAYS", "180")))
    sandbox_repo: str = field(default_factory=lambda: os.getenv("SANDBOX_REPO", "Razeefshaik/premise-switch"))
    sandbox_clone_dir: str = field(default_factory=lambda: os.getenv("SANDBOX_CLONE_DIR", ".runtime/kestrel"))
    github_token: str = field(default_factory=lambda: os.getenv("GITHUB_TOKEN", os.getenv("GH_TOKEN", "")), repr=False)
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""), repr=False)
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "claude-sonnet-5-5"))
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "anthropic"))
    codex_binary: str = field(default_factory=lambda: os.getenv("CODEX_BINARY", ""))
    codex_model: str = field(default_factory=lambda: os.getenv("CODEX_MODEL", ""))
    codex_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("CODEX_TIMEOUT_SECONDS", "180")))
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/"))
    poll_seconds: int = field(default_factory=lambda: int(os.getenv("POLL_SECONDS", "15")))
    auto_fix_pr: bool = field(default_factory=lambda: os.getenv("AUTO_FIX_PR", "false").lower() == "true")
    poll_enabled: bool = field(default_factory=lambda: os.getenv("POLL_ENABLED", "true").lower() == "true")
    steward_enabled: bool = field(default_factory=lambda: os.getenv("STEWARD_ENABLED", "false").lower() == "true")

    def __post_init__(self):
        if self.public_demo:
            for name, value in {"poll_enabled": False, "auto_fix_pr": False,
                                "steward_enabled": False, "llm_provider": "none"}.items():
                object.__setattr__(self, name, value)
        if self.llm_provider not in {"anthropic", "codex-cli", "none"}:
            raise ValueError("LLM_PROVIDER must be anthropic, codex-cli, or none")
        if not 1 <= self.codex_timeout_seconds <= 600:
            raise ValueError("CODEX_TIMEOUT_SECONDS must be between 1 and 600")

    @property
    def database_options(self):
        """Connection options shared by FalkorDB and Redis; never log this mapping."""
        options = {}
        if self.falkordb_username:
            options["username"] = self.falkordb_username
        if self.falkordb_password:
            options["password"] = self.falkordb_password
        if self.falkordb_tls:
            options.update(ssl=True, ssl_cert_reqs="required", ssl_check_hostname=True)
        return options

    @property
    def llm_enabled(self):
        return self.llm_provider == "codex-cli" or (self.llm_provider == "anthropic" and bool(self.anthropic_api_key))


def settings() -> Settings:
    load_dotenv(override=False)
    return Settings()
