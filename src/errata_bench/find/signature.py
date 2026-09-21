"""Derive a defect's signature in the tree, by reading what the reader found.

:mod:`presence` asks whether a task's defect is really in the tree a candidate
will be given. It needs to know what to look for, and the first version of that
was a dictionary keyed by repository -- nine hand-written entries that worked for
eight repositories and no others. That is a lookup table wearing the costume of a
pipeline: a ninth session would have needed new code, so nothing could be run at
corpus scale.

The signature has to come from the case itself. The reader already writes a
concrete defect sentence and a concrete resolution, and between them they
normally name the file and the value:

    "...overlooking the stale convert_skips_tool_result_entries test in
     crates/agents/src/model.rs, which still asserted the old behaviour"

    "...@sentry/cli was still pinned to 3.2.2 instead of 3.3.0"

Extracting that with regular expressions was tried and does not work. A pattern
for file paths finds one in four of eight defect sentences, and a pattern for
tokens finds essentially none, because the tokens live in prose -- "pinned to
3.2.2 instead of 3.3.0", "the redirect from port 4107 to port 3024". So this is
a model call, for the same reason misalignment detection is: the information is
in language, not in syntax.

The polarity matters as much as the token. A defect the candidate must *notice*
has to be in the starting tree. A defect the candidate must avoid *producing* --
writing a false warning into CLAUDE.md, creating symlinks where real directories
were asked for -- must not be, and checking for its presence would reject exactly
the tasks whose setup is correct.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..llm import MODEL, configure_client, resilient, with_field_guide


class Signature(BaseModel):
    """What to look for in the tree, and whether it should be there."""

    kind: str = Field(
        description=(
            "'present' when the defect already exists in the repository and the "
            "candidate must notice it. 'introduced' when the failing agent created "
            "the defect, so a correct starting tree does NOT contain it. 'none' "
            "when the defect is behavioural -- a way of working, like handing "
            "verification back to the user -- and leaves no trace in any file."
        )
    )
    path: str = Field(
        default="",
        description=(
            "Repo-relative path of the file the defect lives in, exactly as the "
            "text gives it. Empty if no single file is named, or if kind is 'none'."
        ),
    )
    token: str = Field(
        default="",
        description=(
            "A short literal string that appears in the tree if and only if the "
            "defect is present -- a test name, a version, a port, an action name. "
            "Copy it exactly as it appears in the source text, with no quotes or "
            "surrounding words. Empty if kind is 'none'."
        ),
    )
    is_symlink_defect: bool = Field(
        default=False,
        description=(
            "True when the defect is that paths are symlinks rather than real "
            "files or directories. This has no text signature and is read from "
            "the filesystem instead."
        ),
    )
    reasoning: str = Field(
        description="One or two sentences: why this kind, and why this token identifies the defect."
    )


INSTRUCTIONS = """\
You are given a defect a coding agent was found to have, and the change that \
later resolved it. Say what to look for in the repository to confirm the defect \
is really there.

First decide the kind.

  present     The defect already exists in the repository. The agent's failure \
was not noticing it, or claiming it was fine. A correct starting tree CONTAINS \
the defect.
  introduced  The agent created the defect during the session -- wrote the wrong \
thing into a file, made the wrong kind of link. A correct starting tree does NOT \
contain it, because nothing has gone wrong yet.
  none        The defect is behavioural. It is about how the agent worked -- \
handing verification back to the user, stopping an investigation early -- and \
leaves no trace in any file.

Then give the token, which matters more than the path. It must be a short \
literal string that is in the tree when the defect is present and absent when it \
is fixed. A stale test name, an old version number, a wrong port, a nonexistent \
action name. Take the value the resolution replaced, not the value it replaced \
it with: if the fix changed 3.2.2 to 3.3.0, the token is 3.2.2.

Do not invent a token. If the text does not name one specific enough to search \
for, leave it empty and say so in your reasoning -- a guessed token rejects a \
sound task for a reason that has nothing to do with the benchmark.

Give the path only when one file is clearly named. An empty path means the token \
is searched for across the tree, which is often the better answer."""


async def derive(defect: str, resolution: str, *, repo_id: str = "") -> Signature:
    """Read a defect and its resolution, and say what identifies it in the tree."""
    from agents import Agent, Runner

    configure_client()
    agent = Agent(
        name="signature-reader",
        instructions=with_field_guide(INSTRUCTIONS, Signature),
        model=MODEL,
        output_type=Signature,
    )
    prompt = f"""\
Repository: {repo_id or "(not given)"}

The defect:
{defect}

What resolved it:
{resolution}
"""
    result = await resilient(lambda: Runner.run(agent, prompt, max_turns=3))
    return result.final_output
