"""Local Codex CLI runner for playlist AI jobs (ChatGPT subscription auth)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


class CodexCliError(RuntimeError):
    """Raised when the Codex CLI is missing, unauthenticated, or returns bad output."""


@dataclass(frozen=True, slots=True)
class CodexCliConfig:
    binary: str = "codex"
    timeout_seconds: int = 180
    sandbox: str = "read-only"


def schema_path(name: str) -> Path:
    """Return path to a packaged Codex output schema JSON file."""

    return Path(__file__).resolve().parent / "codex_schemas" / name


class CodexCliRunner:
    """Run `codex exec` with ChatGPT-login auth and a JSON output schema."""

    def __init__(
        self,
        *,
        config: CodexCliConfig | None = None,
        run_subprocess: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._config = config or CodexCliConfig()
        self._run = run_subprocess or subprocess.run

    def ensure_ready(self) -> None:
        if os.environ.get("OPENAI_API_KEY") or os.environ.get("CODEX_API_KEY"):
            raise CodexCliError(
                "OPENAI_API_KEY / CODEX_API_KEY is set. Unset them so Codex uses "
                "ChatGPT subscription auth instead of API billing."
            )
        binary = shutil.which(self._config.binary)
        if not binary:
            raise CodexCliError(
                f"Codex CLI not found ({self._config.binary!r}). "
                "Install Codex and run `codex login` with your ChatGPT account."
            )
        result = self._run(
            [self._config.binary, "login", "status"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=_clean_env(),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise CodexCliError(
                "Codex is not logged in. Run `codex login` with ChatGPT/Pro. "
                + (detail or "")
            )

    def run_json(
        self,
        *,
        prompt: str,
        schema_file: Path,
        cwd: Path | None = None,
    ) -> dict[str, Any]:
        if not schema_file.is_file():
            raise CodexCliError(f"Codex output schema missing: {schema_file}")

        self.ensure_ready()

        with tempfile.TemporaryDirectory(prefix="playlist-codex-") as tmp:
            out_path = Path(tmp) / "output.json"
            command = [
                self._config.binary,
                "exec",
                "--ephemeral",
                "--sandbox",
                self._config.sandbox,
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_file),
                "-o",
                str(out_path),
                "-",
            ]
            result = self._run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._config.timeout_seconds,
                check=False,
                cwd=str(cwd) if cwd else None,
                env=_clean_env(),
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()
                raise CodexCliError(
                    f"codex exec failed (exit {result.returncode}): {detail or 'no output'}"
                )
            if not out_path.is_file():
                raise CodexCliError("codex exec did not write --output-last-message file")
            try:
                payload = json.loads(out_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise CodexCliError(f"codex output was not valid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise CodexCliError("codex output must be a JSON object")
            return payload


def wrap_ok_output(output: Mapping[str, Any]) -> dict[str, Any]:
    """Wrap a Codex schema payload into the status/output shape parsers expect."""

    return {"status": "ok", "output": dict(output)}


def build_album_copy_prompt(job: Mapping[str, Any]) -> str:
    context = job.get("context") if isinstance(job.get("context"), Mapping) else {}
    instructions = str(job.get("instructions") or "").strip()
    return (
        f"{instructions}\n\n"
        "Job context (do not change title or track_uris):\n"
        f"{json.dumps(context, ensure_ascii=True, indent=2)}\n"
    )


def build_research_competition_prompt(job: Mapping[str, Any]) -> str:
    context = job.get("context") if isinstance(job.get("context"), Mapping) else {}
    instructions = str(job.get("instructions") or "").strip()
    return (
        f"{instructions}\n\n"
        "Competition job context:\n"
        f"{json.dumps(context, ensure_ascii=True, indent=2)}\n"
    )


def build_album_competition_prompt(job: Mapping[str, Any]) -> str:
    context = job.get("context") if isinstance(job.get("context"), Mapping) else {}
    instructions = str(job.get("instructions") or "").strip()
    return (
        f"{instructions}\n\n"
        "Album competition job context (live Spotify search results included):\n"
        f"{json.dumps(context, ensure_ascii=True, indent=2)}\n"
    )


def _clean_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if value is not None}
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    return env
