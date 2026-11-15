"""Versioned Jinja2 prompt templates for agents (Stage 26).

A prompt template is one `.j2` file whose first line is `{# version: N #}` (N a positive
integer) and which defines two blocks, `system` and `user`: `PromptRegistry.render` renders
each block into one message. Shared partials live in `agents/prompts/_partials/` and carry a
version line too. The version line is removed before Jinja2 sees the source, so it adds no
blank line. Undefined variables fail (`StrictUndefined`): a prompt is never sent with a
silently empty field.

Every template sees two globals: `data_block(text, source=...)` (the Stage 24
prompt-injection wrapper) and `evidence_lines(items)` (`[E:<id>] ...` lines).
"""

import hashlib
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jinja2
from jinja2 import meta

from atlas.agents.evidence import evidence_lines
from atlas.core.errors import ConfigError
from atlas.llm.hygiene import data_block
from atlas.llm.schemas import Message

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
"""The prompt templates shipped with Atlas (`_partials/` holds the shared ones)."""

VERSION_RE = re.compile(r"\A\{#\s*version:\s*([1-9][0-9]*)\s*#\}[ \t]*(?:\r?\n|\Z)")
"""The required first line of every template and partial: `{# version: N #}`, N >= 1."""

BLOCKS = ("system", "user")
"""The blocks a prompt template must define: the system and the user message."""


class _VersionedLoader(jinja2.FileSystemLoader):
    """A file loader that requires the version line, records the version and removes the
    line from the source handed to Jinja2."""

    def __init__(self, searchpath: Sequence[Path]) -> None:
        super().__init__([str(p) for p in searchpath], encoding="utf-8")
        self.versions: dict[str, int] = {}

    def get_source(
        self, environment: jinja2.Environment, template: str
    ) -> tuple[str, str, Callable[[], bool]]:
        source, filename, uptodate = super().get_source(environment, template)
        found = VERSION_RE.match(source)
        if found is None:
            raise ConfigError(
                f"prompt template {template!r} ({filename}): the first line must be the "
                "version line '{# version: N #}' with N a positive integer"
            )
        self.versions[template] = int(found.group(1))
        return source[found.end() :], filename, uptodate


@dataclass(frozen=True)
class PromptTemplate:
    """A loaded prompt template: its `name` (path relative to a prompt folder), `version`
    (from its version line), `digest` (SHA-256 over the name, version and source of the
    template and of every template it includes or imports) and those `sources` (names)."""

    name: str
    version: int
    digest: str
    sources: tuple[str, ...]

    @property
    def label(self) -> str:
        """`<name>@v<version>`, for traces and the LLM cache's prompt version."""
        return f"{self.name}@v{self.version}"


@dataclass(frozen=True)
class RenderedPrompt:
    """The `system` and `user` texts rendered from `template`."""

    template: PromptTemplate
    system: str
    user: str

    @property
    def messages(self) -> tuple[Message, Message]:
        """The system message and the user message."""
        return Message.system(self.system), Message.user(self.user)


class PromptRegistry:
    """Loads, versions and renders prompt templates.

    Templates are looked up in `search` folders first (in order), then in `PROMPTS_DIR`, so a
    test or a later stage can add its own templates and override a shipped one. Loaded
    templates are cached; the registry is safe to share between threads.
    """

    def __init__(self, search: Sequence[Path] = ()) -> None:
        self._folders = (*(Path(p) for p in search), PROMPTS_DIR)
        self._loader = _VersionedLoader(self._folders)
        self._env = jinja2.Environment(
            loader=self._loader,
            undefined=jinja2.StrictUndefined,
            autoescape=False,
            keep_trailing_newline=False,
            # A loaded template never changes under a running process: the recorded version
            # and digest must describe the text that is sent (audit A2).
            auto_reload=False,
        )
        self._env.globals.update(data_block=data_block, evidence_lines=evidence_lines)
        self._lock = threading.Lock()
        self._loaded: dict[str, PromptTemplate] = {}

    @property
    def folders(self) -> tuple[Path, ...]:
        """Where templates are looked up, in order."""
        return self._folders

    def _template(self, name: str) -> jinja2.Template:
        try:
            return self._env.get_template(name)
        except jinja2.TemplateNotFound as exc:
            raise ConfigError(
                f"prompt template {name!r} not found in {[str(f) for f in self._folders]}"
            ) from exc
        except jinja2.TemplateSyntaxError as exc:
            raise ConfigError(f"prompt template {name!r} has a syntax error: {exc}") from exc

    def _sources(self, name: str) -> list[tuple[str, int, str]]:
        """`(name, version, source)` of `name` and of every template it references."""
        seen: dict[str, tuple[str, int, str]] = {}
        todo = [name]
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            self._template(current)  # loads it: checks the version line and the syntax
            source, _, _ = self._loader.get_source(self._env, current)
            seen[current] = (current, self._loader.versions[current], source)
            for ref in meta.find_referenced_templates(self._env.parse(source)):
                if ref is None:
                    raise ConfigError(
                        f"prompt template {current!r} has a dynamic include or import: name "
                        "included templates literally so the prompt digest covers them"
                    )
                todo.append(ref)
        return [seen[key] for key in sorted(seen)]

    def get(self, name: str) -> PromptTemplate:
        """The template `name` (for example `fundamental_analyst.j2`). Raises `ConfigError`
        when it is missing, lacks a version line (it or a partial), has a syntax error,
        includes templates dynamically or does not define the `system` and `user` blocks."""
        with self._lock:
            if name in self._loaded:
                return self._loaded[name]
            template = self._template(name)
            missing = [block for block in BLOCKS if block not in template.blocks]
            if missing:
                raise ConfigError(f"prompt template {name!r} must define the blocks {missing}")
            sources = self._sources(name)
            digest = hashlib.sha256()
            for ref, version, source in sources:
                digest.update(f"{ref}\0v{version}\0{source}\0".encode())
            loaded = PromptTemplate(
                name=name,
                version=self._loader.versions[name],
                digest=digest.hexdigest(),
                sources=tuple(ref for ref, _, _ in sources),
            )
            self._loaded[name] = loaded
            return loaded

    def version_of(self, name: str) -> int:
        """The version of the template or partial `name`."""
        self._template(name)
        return self._loader.versions[name]

    def render(self, name: str, variables: Mapping[str, Any]) -> RenderedPrompt:
        """Render the `system` and `user` blocks of `name` with `variables` (each stripped of
        surrounding whitespace). Raises `ConfigError` for an undefined variable or a block
        that renders empty."""
        loaded = self.get(name)
        template = self._template(name)
        texts = []
        for block in BLOCKS:
            try:
                context = template.new_context(dict(variables))
                text = "".join(template.blocks[block](context)).strip()
            except jinja2.UndefinedError as exc:
                raise ConfigError(f"prompt template {name!r}, block {block!r}: {exc}") from exc
            if not text:
                raise ConfigError(f"prompt template {name!r}: block {block!r} rendered empty")
            texts.append(text)
        return RenderedPrompt(template=loaded, system=texts[0], user=texts[1])
