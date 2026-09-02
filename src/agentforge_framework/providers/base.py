"""The Provider port: one Role, one invocation, one result.

ADR-0001 makes every Agent a subprocess call to a coding-agent CLI. `Provider`
is the whole of that contract — no streaming, no session, no conversation state.
A Role, a prompt, a Context Pack, a Model Tier, and a working directory go in;
an `AgentResult` comes out.

Two things are deliberately split here:

- The **envelope** — how a CLI reports its own success, and where in its output
  the model's text is — belongs to the adapter. A version bump breaks one
  adapter rather than the framework.
- The **result block** — the delimited JSON a Role is instructed to end with —
  belongs to AgentForge. It travels in the prompt, so every adapter gets it for
  free and none of them has to invent an escalation convention.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from ..core.config import CapabilityTier, Config
from ..core.contracts import (
    AgentResult,
    ContextPack,
    Finding,
    ModelTier,
    Outcome,
    Role,
    Usage,
)
from ..core.plan_format import extract_result_block
from ..core.process import CommandResult, CommandRunner, MissingBinary, require
from ..core.skills import expand, read_skill, split_delivery


class ProviderError(RuntimeError):
    """A Provider could not be used at all, as distinct from an Agent failing."""


class Provider(ABC):
    """The port. One method, and it is the only thing a Role knows about."""

    name: ClassVar[str] = "provider"
    binary: ClassVar[str] = ""

    #: Whether an Agent may run commands, not merely edit files. Default-deny
    #: per ADR-0007: opened for one Run by an explicit flag, and expressed in
    #: the argument vector rather than in prompt text, because a permission
    #: written as an instruction is one the model can talk itself out of.
    allow_commands: bool = False

    @abstractmethod
    def invoke(
        self,
        *,
        role: Role,
        prompt: str,
        context: ContextPack,
        tier: ModelTier,
        cwd: Path,
    ) -> AgentResult: ...

    def preflight(self) -> None:
        """Confirm the CLI exists, naming it if not. Called before a Run starts."""


@dataclass(frozen=True)
class ProviderOutput:
    """What an adapter recovered from its CLI's envelope.

    `usage` is what the CLI said the invocation consumed, and it is recovered
    here rather than centrally because the envelope is the adapter's business:
    ADR-0009 keeps a third Provider from adding a third place that knows how the
    second one reports itself. `None` means the CLI reported nothing, which is
    not the same as a free invocation.
    """

    text: str
    error: str | None = None
    usage: Usage | None = None


class CliProvider(Provider):
    """Shared plumbing for adapters that shell out to a coding-agent CLI.

    Subclasses supply three small things: the tier-to-model mapping, the
    argument vector, and how to get the model's text out of the CLI's envelope.
    Everything else — turning that text into an `AgentResult` — is the shared
    result-block contract.

    Extending this is a convenience, not a requirement. An adapter that
    implements `invoke` directly is equally valid, and writing one that way is
    the honest test of whether the port is portable or merely Claude-shaped.
    """

    #: Tier to model identifier. Nothing outside an adapter knows these strings.
    models: ClassVar[dict[ModelTier, str]] = {}

    def __init__(
        self,
        runner: CommandRunner,
        timeout: float | None = 1800.0,
        allow_commands: bool = False,
        config: Config | None = None,
    ) -> None:
        self.runner = runner
        self.timeout = timeout
        self.allow_commands = allow_commands
        self.capability_tier = (config or Config()).capability_for(self.name)

    def preflight(self) -> None:
        try:
            require(
                self.runner,
                self.binary,
                f"AgentForge drives the {self.name} CLI (ADR-0001); install it or "
                f"select another provider with --provider.",
            )
        except MissingBinary as exc:
            raise ProviderError(str(exc)) from exc

    def model_for(self, tier: ModelTier) -> str:
        try:
            return self.models[tier]
        except KeyError as exc:
            raise ProviderError(
                f"the {self.name} adapter has no model for tier {tier!r}"
            ) from exc

    @abstractmethod
    def build_argv(
        self, prompt: str, model: str, native_skills: tuple[str, ...] = ()
    ) -> Sequence[str]: ...

    @abstractmethod
    def parse_output(self, result: CommandResult) -> ProviderOutput: ...

    def invoke(
        self,
        *,
        role: Role,
        prompt: str,
        context: ContextPack,
        tier: ModelTier,
        cwd: Path,
    ) -> AgentResult:
        prompt, native_skills = self._deliver_skills(role, prompt)
        argv = self.build_argv(prompt, self.model_for(tier), native_skills)
        completed = self.runner.run(argv, cwd=cwd, timeout=self.timeout)
        output = self.parse_output(completed)
        return to_agent_result(role=role, tier=tier, output=output)

    def _deliver_skills(self, role: Role, prompt: str) -> tuple[str, tuple[str, ...]]:
        """Validate and deliver the Role's skills before the CLI is invoked.

        A native Provider is named the skills the Role declared and no more: a
        composite fans out to its parts through the CLI's own Skill mechanism,
        which is what the mechanism is for.

        A Fragment Provider has no such mechanism, so the composite is expanded
        here and every body travels. Without that, a skill whose text says "run
        these two" reaches a Provider that cannot run anything and the Role is
        left with an instruction pointing at nothing.

        A skill whose author forbids model invocation is a Fragment whatever the
        tier, so the two deliveries are not exclusive: a Role can declare one of
        each and the prompt carries both.
        """
        if not role.skills:
            return prompt, ()

        if self.capability_tier is CapabilityTier.NATIVE:
            for name in role.skills:
                read_skill(name)  # refuse a name nothing answers for, before the CLI runs
            native, inlined = split_delivery(role.skills)
        else:
            native, inlined = (), tuple(role.skills)

        if native:
            commands = ", ".join(f"/agentforge:{name}" for name in native)
            instruction = (
                f"Use the declared native AgentForge skills before doing this work: {commands}."
            )
            prompt = f"{instruction}\n\n{prompt}"

        if inlined:
            fragments = [
                f"## Skill: {name}\n\n{read_skill(name).rstrip()}" for name in expand(inlined)
            ]
            prompt = f"{prompt}\n\n" + "\n\n".join(fragments) + "\n"

        return prompt, native


def to_agent_result(*, role: Role, tier: ModelTier, output: ProviderOutput) -> AgentResult:
    """Turn a Role's text output into a result the runtime can act on.

    A Role that reports nothing parseable is a failure, not a quiet success. The
    alternative — treating unstructured text as a completed step — is how a Run
    opens a pull request containing no changes and says it worked.

    Every way out of here carries the usage the adapter recovered, failures
    included. A Run that spent four dollars discovering its CLI was misconfigured
    spent four dollars, and a total that quietly omitted them would be the one
    figure a reader most wants.
    """
    if output.error:
        return AgentResult(
            role=role.name,
            tier=tier,
            outcome=Outcome.FAILED,
            summary=output.error,
            detail=output.text,
            raw=output.text,
            usage=output.usage,
        )

    payload = extract_result_block(output.text)
    if payload is None:
        return AgentResult(
            role=role.name,
            tier=tier,
            outcome=Outcome.FAILED,
            summary=(
                f"the {role.name} Agent finished without reporting a result block, "
                "so AgentForge cannot tell what it did"
            ),
            detail=output.text,
            raw=output.text,
            usage=output.usage,
        )

    try:
        outcome = Outcome(str(payload.get("outcome", "")).strip().lower())
    except ValueError:
        outcome = Outcome.FAILED

    summary = str(payload.get("summary") or "").strip()
    if outcome is Outcome.FAILED and not summary:
        summary = f"the {role.name} Agent reported an unrecognized outcome"

    return AgentResult(
        role=role.name,
        tier=tier,
        outcome=outcome,
        summary=summary,
        detail=str(payload.get("detail") or ""),
        files_changed=tuple(payload.get("files_changed") or ()),
        findings=tuple(Finding.coerce(item) for item in payload.get("findings") or ()),
        raw=output.text,
        usage=output.usage,
    )


__all__ = ["CliProvider", "Provider", "ProviderError", "ProviderOutput", "to_agent_result"]
