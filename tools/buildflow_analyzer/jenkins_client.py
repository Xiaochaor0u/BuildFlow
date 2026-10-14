"""Jenkins REST API client (standard library only).

Credentials are read from the environment by :func:`Config.from_env`; nothing is
hard-coded and no secret is ever written into a report or a log.
"""

from __future__ import annotations

import base64
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


class JenkinsError(RuntimeError):
    """Raised when Jenkins cannot be reached or returns an unexpected payload."""


@dataclass
class Config:
    """Where to read from and how much to read."""

    url: str
    job: str
    user: Optional[str] = None
    token: Optional[str] = None
    limit: int = 50
    timeout: int = 30
    verify_tls: bool = True
    fetch_logs: bool = True
    max_log_chars: int = 400_000

    @staticmethod
    def from_env(**overrides: Any) -> "Config":
        """Build a config from environment variables, with CLI overrides winning.

        Recognised variables:
          JENKINS_URL       base URL, e.g. http://127.0.0.1:8080
          JENKINS_USER      user name
          JENKINS_TOKEN     API token or password
          JENKINS_JOB       job name, e.g. buildflow-ci
          JENKINS_BUILD_LIMIT  how many builds to read
        """
        def pick(key: str, default: Any) -> Any:
            value = overrides.get(key)
            if value is not None:
                return value
            return default

        url = pick("url", os.environ.get("JENKINS_URL"))
        if not url:
            raise JenkinsError(
                "Jenkins URL is required: pass --url or set JENKINS_URL"
            )
        job = pick("job", os.environ.get("JENKINS_JOB"))
        if not job:
            raise JenkinsError(
                "Jenkins job is required: pass --job or set JENKINS_JOB"
            )

        limit_raw = pick("limit", os.environ.get("JENKINS_BUILD_LIMIT", 50))
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            raise JenkinsError(f"invalid build limit: {limit_raw!r}")
        if limit <= 0:
            raise JenkinsError("build limit must be positive")

        return Config(
            url=str(url).rstrip("/"),
            job=str(job).strip("/"),
            user=pick("user", os.environ.get("JENKINS_USER")),
            token=pick("token", os.environ.get("JENKINS_TOKEN")),
            limit=limit,
            timeout=int(pick("timeout", 30)),
            verify_tls=bool(pick("verify_tls", True)),
            fetch_logs=bool(pick("fetch_logs", True)),
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.user and self.token)

    def describe(self) -> str:
        """Safe description for logs and reports (never includes the secret)."""
        auth = f"user={self.user}" if self.user else "anonymous"
        return f"{self.url} job={self.job} limit={self.limit} auth={auth}"


class JenkinsClient:
    """Minimal Jenkins REST client covering the endpoints this tool needs."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._opener = self._build_opener()

    # ------------------------------------------------------------------ plumbing
    def _build_opener(self) -> urllib.request.OpenerDirector:
        handlers: list[Any] = []
        if not self.config.verify_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        opener = urllib.request.build_opener(*handlers)
        if self.config.has_credentials:
            raw = f"{self.config.user}:{self.config.token}".encode("utf-8")
            header = "Basic " + base64.b64encode(raw).decode("ascii")
            opener.addheaders = [("Authorization", header)]
        return opener

    def _get(self, path: str, *, raw: bool = False) -> Any:
        url = f"{self.config.url}/{path.lstrip('/')}"
        request = urllib.request.Request(url)
        try:
            with self._opener.open(request, timeout=self.config.timeout) as resp:
                payload = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise JenkinsError(
                    "authentication failed (401): check JENKINS_USER / JENKINS_TOKEN"
                ) from exc
            if exc.code == 403:
                raise JenkinsError(
                    f"access denied (403) for {url}: the account lacks permission"
                ) from exc
            if exc.code == 404:
                raise JenkinsError(f"not found (404): {url}") from exc
            raise JenkinsError(f"HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise JenkinsError(f"cannot reach Jenkins at {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise JenkinsError(f"timeout after {self.config.timeout}s: {url}") from exc

        if raw:
            return payload.decode("utf-8", errors="replace")
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise JenkinsError(f"invalid JSON from {url}: {exc}") from exc

    @staticmethod
    def _quote_job(job: str) -> str:
        """Encode a job name, keeping '/' separators for nested folders."""
        return "/".join(
            urllib.parse.quote(part, safe="") for part in job.split("/") if part
        )

    # ------------------------------------------------------------------ endpoints
    def job_info(self) -> dict:
        path = f"job/{self._quote_job(self.config.job)}/api/json"
        tree = "name,url,color,builds[number,result,building]"
        return self._get(f"{path}?tree={urllib.parse.quote(tree)}")

    def builds(self) -> list[dict]:
        """Build summaries, newest first, capped at ``config.limit``."""
        path = f"job/{self._quote_job(self.config.job)}/api/json"
        tree = (
            "builds[number,result,building,timestamp,duration,"
            "url,displayName,id,estimatedDuration]"
        )
        data = self._get(f"{path}?tree={urllib.parse.quote(tree)}")
        builds = data.get("builds") or []
        return builds[: self.config.limit]

    def build_detail(self, number: int) -> dict:
        """One build with the fields used for commit extraction."""
        path = f"job/{self._quote_job(self.config.job)}/{number}/api/json"
        tree = "number,result,building,timestamp,duration,url,actions[_class,lastBuiltRevision[SHA1,branch[name]],remoteUrls]"
        return self._get(f"{path}?tree={urllib.parse.quote(tree)}")

    def console_text(self, number: int) -> str:
        path = f"job/{self._quote_job(self.config.job)}/{number}/consoleText"
        text = self._get(path, raw=True)
        if len(text) > self.config.max_log_chars:
            # Keep the head (stage declarations) and the tail (the actual error).
            head = text[: self.config.max_log_chars // 4]
            tail = text[-(self.config.max_log_chars * 3 // 4):]
            return head + "\n...[console truncated by buildflow-analyzer]...\n" + tail
        return text


def extract_commit(detail: dict) -> tuple[Optional[str], Optional[str]]:
    """Return ``(sha, branch)`` from a build's git action, when present.

    Jenkins exposes the checked-out revision through the Git plugin's
    ``hudson.plugins.git.util.BuildData`` action.
    """
    for action in detail.get("actions") or []:
        if not isinstance(action, dict):
            continue
        cls = action.get("_class") or ""
        if "git" not in cls.lower():
            continue
        revision = action.get("lastBuiltRevision") or {}
        sha = revision.get("SHA1")
        branch = None
        for entry in revision.get("branch") or []:
            if isinstance(entry, dict) and entry.get("name"):
                branch = entry["name"]
                break
        if sha:
            return sha, branch
    return None, None
