import dataclasses
import json
from pathlib import Path


@dataclasses.dataclass
class Config:
    dataset: str = "data/ahc001"
    run_dir: str = "runs/demo"
    provider: str = "offline"
    backend: str = "native"
    iterations: int = 24
    seed: int = 42
    islands: int = 3
    migration_interval: int = 6
    smoke_cases: int = 2
    screen_margin: float = 0.04
    screen_exploration: float = 0.15
    repairs: int = 1
    workers: int = 2
    timeout: float = 5.0
    docker_image: str = "python:3.11-slim"
    base_url: str = ""
    model: str = ""
    api_key_env: str = "AAD_API_KEY"
    max_requests: int = 40
    max_output_tokens: int = 8192
    request_timeout: float = 120.0
    request_options: dict = dataclasses.field(default_factory=dict)
    allow_unsafe_native: bool = False

    def validate(self):
        if self.provider not in {"offline", "chat"} or self.backend not in {"native", "docker", "ale"}:
            raise ValueError("Unknown provider or backend")
        for name in ("iterations", "islands", "migration_interval", "smoke_cases", "workers",
                     "max_requests", "max_output_tokens"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.timeout <= 0 or self.request_timeout <= 0 or self.repairs < 0:
            raise ValueError("Invalid timeout or repair budget")
        if not 0 <= self.screen_exploration <= 1 or not 0 <= self.screen_margin <= 1:
            raise ValueError("Invalid screening configuration")
        if self.provider == "chat" and self.backend == "native" and not self.allow_unsafe_native:
            raise ValueError("LLM-generated programs require backend docker/ale. Native execution is not a security sandbox; explicitly set allow_unsafe_native only in a disposable environment.")
        if self.provider == "chat" and (not self.base_url or not self.model):
            raise ValueError("Configure base_url and model for the chat provider")

    @classmethod
    def load(cls, path: Path):
        return cls(**json.loads(path.read_text(encoding="utf-8")))

    def as_dict(self):
        return dataclasses.asdict(self)
