import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Settings:
    runtime: Path = field(default_factory=lambda: Path(os.environ.get('KASSA_RUNTIME', ROOT / 'runtime')))
    api_key: str | None = None
    max_upload_bytes: int = 256 * 1024 * 1024
    max_duration_sec: float = 180
    max_pending: int = 2
    fps: float = 1
    window_sec: float = 12
    overlap_sec: float = 2
    frame_count: int = 6
    max_job_sec: float = 285

    def __post_init__(self):
        self.runtime = self.runtime.resolve()
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.runtime / "jobs").mkdir(exist_ok=True, mode=0o700)
        if self.api_key is None:
            self.api_key = os.environ.get("KASSA_API_KEY")
        if not self.api_key:
            key_file = Path(os.environ.get('KASSA_KEY_FILE', self.runtime / 'api.key'))
            try:
                fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                self.api_key = key_file.read_text().strip()
            else:
                self.api_key = secrets.token_urlsafe(32)
                with os.fdopen(fd, "w") as stream:
                    stream.write(self.api_key + "\n")
        if not self.api_key:
            raise ValueError("API key cannot be empty")
        # curl supports -H @file; this avoids putting the key in process arguments.
        header = self.runtime / "api-key.header"
        fd = os.open(header, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("X-API-Key: " + self.api_key + "\n")

    @property
    def database(self):
        return self.runtime / "jobs.sqlite3"
