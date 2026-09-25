"""Talking to the model: which one, how, and what to do when it says nothing.

This was the top of reader.py, and reader.py was imported by eleven modules --
ten of which wanted only this. `configure_client` happened to be written next
to the reader, so every gate, judge and probe in the package imported the
reader to reach it, and the import graph looked like a hairball for no reason
beyond where a function had been put.

Taken by its importers before the split:

    MODEL 10 - configure_client 10 - with_field_guide 9 - load_session_turns 8
    judge_model 3 - build_excerpt 3 - resilient 2 - read_pushback 1
"""

from __future__ import annotations

import os
import re
from pathlib import Path


REQUEST_TIMEOUT_S = 120.0
MAX_RETRIES = 1
_client_configured = False


_DEFAULT_MODEL = "gpt-6-astra"


def _load_dotenv() -> None:
    """Read .env into the environment, without overriding what is already set.

    The credential was reachable from an interactive shell and absent from a
    backgrounded one, so a batch of eleven trajectory readings failed with
    "Missing credentials" and recorded eleven unusable trajectories. Nothing was
    wrong with the data: the key simply depended on how the process happened to
    be launched. Reading the file here makes that path the same either way.

    An exported variable wins over the file, so a caller can still override it,
    and python-dotenv is not worth a dependency for a KEY=value file.

    ERRATA_DOTENV=0 skips the file. The guard suite sets it (B-264): read at
    import, the file handed the suite a real key that CI never has, and a stand-in
    that stopped standing in called the provider with it.
    """
    import os

    if os.environ.get("ERRATA_DOTENV") == "0":
        return
    from .project import ROOT

    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


# Sampling is not configurable on this model: passing temperature returns
# "Unsupported parameter: 'temperature' is not supported with this model". The
# gates were suspected of being unstable for that reason; measuring instead of
# assuming showed they are not. Five runs of the leakage gate over twenty-seven
# trajectories agreed five times out of five on twenty-six of them.
#
# The one that moved -- marin-community at turn 285, three leaks in five runs --
# is genuinely borderline rather than noisy, and a task that close to the line
# should be excluded rather than admitted on a coin flip.

_load_dotenv()

# Resolved at import because twelve call sites bind it as a default argument.
# On Azure this must be a *deployment* name: Azure routes by deployment, not by
# model, and a request naming a model with no matching deployment fails with
# DeploymentNotFound however valid the model is.
MODEL = os.environ.get("ERRATA_MODEL") or _DEFAULT_MODEL
# ERRATA_MODEL exists because Azure routes by *deployment* name, not model name:
# a request naming a real model fails with DeploymentNotFound unless a
# deployment carries that name. Unset, the default is unchanged.


def model_name() -> str:
    """The model to call, which on Azure is a deployment name.

    Azure routes by deployment, not by model: a request naming `gpt-5` fails
    with DeploymentNotFound unless a deployment is literally called that. So the
    name is configurable, and the default only applies to the direct API.
    """
    import os

    _load_dotenv()
    refuse_claude(os.environ.get("ERRATA_MODEL") or MODEL)
    return os.environ.get("ERRATA_MODEL") or MODEL


def judge_model() -> str:
    """Which model grades, when that is not the model being graded.

    One setting used to name both, so a candidate could only ever be marked by
    itself -- the self-grading this benchmark set out to measure. Unset, this is
    the candidate's model and nothing changes; set, it names the grader for the
    judge, the trace check, calibration and the controls alike, so every task
    and every answer in a run is marked by the same reader.
    """
    import os

    _load_dotenv()
    refuse_claude(os.environ.get("ERRATA_JUDGE_MODEL"))
    return os.environ.get("ERRATA_JUDGE_MODEL") or model_name()


# No request goes to a Claude deployment. On Azure it is billed to the user's
# own card, not to the Azure credits (09-24), and the one key this project
# holds reaches every deployment on the resource, Claude's included. So the
# model a request names is read in the HTTP client, and a Claude one is
# refused before anything is sent -- whoever asks: a stage, a judge, a
# re-grade, a probe. ERRATA_ALLOW_CLAUDE=1 lifts it; nothing here sets it.
class ClaudeRefused(RuntimeError):
    """A call to a Claude deployment, refused before anything was sent."""


def refuse_claude(model) -> None:
    """Raise if ``model`` names a Claude deployment and nobody has lifted the block."""
    if model and "claude" in str(model).lower() and os.environ.get("ERRATA_ALLOW_CLAUDE") != "1":
        raise ClaudeRefused(
            f"refused to call {model}: a Claude deployment on Azure bills the user's own card, "
            "not the Azure credits. Set ERRATA_ALLOW_CLAUDE=1 to override.")


def _refused(e: BaseException) -> ClaudeRefused | None:
    """The refusal behind an exception, if any: the SDK re-raises a hook's error as a connection error."""
    seen = set()
    while e is not None and id(e) not in seen:
        if isinstance(e, ClaudeRefused):
            return e
        seen.add(id(e))
        e = e.__cause__ or e.__context__
    return None


async def _refuse_claude_request(request) -> None:
    """The HTTP client's request hook: the model named in the body or the URL."""
    import json

    refuse_claude(str(request.url) if "claude" in str(request.url).lower() else None)
    try:
        body = json.loads(request.content or b"{}")
    except Exception:  # noqa: BLE001 - a streamed or unreadable body names no model to refuse
        return
    if isinstance(body, dict):
        refuse_claude(body.get("model"))


def _http_client(transport=None):
    """The HTTP client every model call goes through, with the Claude refusal on it."""
    from openai import DefaultAsyncHttpxClient

    return DefaultAsyncHttpxClient(event_hooks={"request": [_refuse_claude_request]},
                                   **({"transport": transport} if transport is not None else {}))


def candidate_client():
    """An API client for a candidate's own calls: set up as `configure_client`'s, retrying nothing.

    The attempt re-sends a throttled or dropped request itself (B-262), so it can
    take the wait off the candidate's time. The client's own retries slept where
    nothing could see them: Kimi-K2.7-Code's attempts ran past their deadline
    on its quota, not its work.
    """
    import os

    from openai import AsyncOpenAI

    _load_dotenv()
    timeout = float(os.environ.get("ERRATA_TIMEOUT") or REQUEST_TIMEOUT_S)
    if os.environ.get("ERRATA_PROVIDER", "").lower() == "azure":
        base, key = os.environ.get("AZURE_OPENAI_BASE_URL"), os.environ.get("AZURE_OPENAI_API_KEY")
        if not base or not key:
            raise RuntimeError("ERRATA_PROVIDER=azure needs AZURE_OPENAI_BASE_URL and AZURE_OPENAI_API_KEY.")
        return AsyncOpenAI(api_key=key, base_url=base.rstrip("/"), timeout=timeout, max_retries=0,
                           http_client=_http_client())
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set and no .env supplies it.")
    return AsyncOpenAI(api_key=key, timeout=timeout, max_retries=0, http_client=_http_client())


def configure_client() -> None:
    """Install an API client that fails fast instead of hanging.

    Azure is used when AZURE_OPENAI_BASE_URL is set, and the direct OpenAI API
    otherwise. Azure's v1 surface accepts `Authorization: Bearer`, verified
    against the live endpoint, so the ordinary client works with a base_url --
    no AsyncAzureOpenAI, no api-version juggling.
    """
    global _client_configured
    if _client_configured:
        return
    import os

    from agents import set_default_openai_client
    from openai import AsyncOpenAI

    _load_dotenv()
    # Both overridable, neither changed. grok-4.6 spends about 4,900 tokens
    # reasoning before a one-paragraph judgement -- 96 seconds on a short task --
    # so a long one crosses the 120-second limit and is retried from scratch.
    # Kimi-K2.7-Code is fast but capped at 50,000 tokens a minute, about six
    # judge calls, so it needs more retries rather than more time.
    timeout = float(os.environ.get("ERRATA_TIMEOUT") or REQUEST_TIMEOUT_S)
    retries = int(os.environ.get("ERRATA_MAX_RETRIES") or MAX_RETRIES)
    # The direct API is the default and is unchanged. Azure is opt-in through
    # ERRATA_PROVIDER=azure, never inferred from the presence of a variable:
    # inferring it from AZURE_OPENAI_BASE_URL silently rerouted every call in
    # the pipeline to a resource with no deployments, and the working path
    # became unreachable while its credential was still sitting there.
    azure = os.environ.get("ERRATA_PROVIDER", "").lower() == "azure"
    if azure:
        base = os.environ.get("AZURE_OPENAI_BASE_URL")
        key = os.environ.get("AZURE_OPENAI_API_KEY")
        if not base or not key:
            raise RuntimeError(
                "ERRATA_PROVIDER=azure needs AZURE_OPENAI_BASE_URL and "
                "AZURE_OPENAI_API_KEY."
            )
        client = AsyncOpenAI(
            api_key=key,
            base_url=base.rstrip("/"),
            timeout=timeout,
            max_retries=retries,
            http_client=_http_client(),
        )
        # The SDK uploads a trace of every run to OpenAI's dashboard by
        # default, whichever provider answered. On Azure that sends the
        # prompts -- transcripts from other people's repositories -- to a
        # second company nobody chose, and it was failing with 401 anyway.
        from agents import set_tracing_disabled

        set_tracing_disabled(True)
    else:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set and no .env supplies it. Refusing to run: "
                "a missing credential otherwise reads as a batch of unusable data."
            )
        client = AsyncOpenAI(api_key=key, timeout=timeout, max_retries=retries, http_client=_http_client())
    set_default_openai_client(client)

    # Which API surface the SDK uses. The default is unchanged -- the Responses
    # API, as before -- and chat completions are selected only when asked for,
    # or on Azure, where the Responses path demonstrably does not work for
    # non-OpenAI deployments: Kimi-K2.7-Code and DeepSeek-V4-Pro both fail with
    # "Invalid JSON when parsing model output" through it, while emitting
    # perfectly valid JSON for the same schema through chat completions. Both
    # surfaces answer, so this is about how structured output is requested, not
    # about reachability.
    api = os.environ.get("ERRATA_API") or (
        "chat_completions" if os.environ.get("ERRATA_PROVIDER", "").lower() == "azure" else ""
    )
    if api:
        from agents import set_default_openai_api

        set_default_openai_api(api)
    _client_configured = True


def usage_of(result) -> dict | None:
    """A model run's token use as a plain row, or None when it reported none (D-36 A6).

    With the cached and the reasoning tokens (B-254): gpt-6-astra bills a cached
    input token at a tenth of the price, and the three readings of one answer
    share their prompt, so the cost cannot be told from the four totals.
    """
    u = getattr(getattr(result, "context_wrapper", None), "usage", None)
    if u is None:
        return None
    return {"requests": getattr(u, "requests", 0), "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0), "total_tokens": getattr(u, "total_tokens", 0),
            "cached_tokens": getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0,
            "reasoning_tokens": getattr(getattr(u, "output_tokens_details", None), "reasoning_tokens", 0) or 0}


def served(model: str) -> dict:
    """Which model a deployment is serving now, from one small call (D-36 A6, G-75).

    The response body echoes the deployment name for most Azure deployments,
    but Azure's headers say more: `x-ms-served-model` (the deployment named
    claude-opus-5 serves claude-opus-5-2) and `azureml-model-session`, a dated
    id that changes when the deployment does. The model library does not pass
    headers on, so each stage asks once at its start and once at its end, and
    a change mid-run shows. Never raises: a probe that could not be made is a
    row saying why, not a stage that stops.
    """
    import datetime

    row = {"deployment": model,
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    try:
        refuse_claude(model)
    except ClaudeRefused as e:
        return {**row, "error": str(e)}
    _load_dotenv()
    azure = os.environ.get("ERRATA_PROVIDER", "").lower() == "azure"
    key = os.environ.get("AZURE_OPENAI_API_KEY" if azure else "OPENAI_API_KEY")
    base = os.environ.get("AZURE_OPENAI_BASE_URL") if azure else None
    if not key or (azure and not base):
        return {**row, "error": "no credential to probe with"}
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key, base_url=base.rstrip("/") if base else None,
                        max_retries=2, timeout=60)
        api = os.environ.get("ERRATA_API") or ("chat_completions" if azure else "responses")
        if api == "responses":
            raw = client.responses.with_raw_response.create(model=model, input="Reply with the word OK.")
        else:
            raw = client.chat.completions.with_raw_response.create(
                model=model, messages=[{"role": "user", "content": "Reply with the word OK."}])
        body, headers = raw.parse(), raw.headers
        return {**row, "served_model": headers.get("x-ms-served-model") or getattr(body, "model", None),
                "model_session": headers.get("azureml-model-session"),
                "region": headers.get("x-ms-region")}
    except Exception as e:  # noqa: BLE001 - provenance is recorded, never fatal
        return {**row, "error": f"{type(e).__name__}: {e}"[:300]}


async def record_served(path, model: str, stage: str, when: str) -> None:
    """Probe `model` and append what it served to `path` (a run's served.jsonl)."""
    import asyncio

    from .store import append

    append(path, {**await asyncio.to_thread(served, model), "stage": stage, "when": when})


def _describe(schema: dict, defs: dict, indent: str = "") -> list[str]:
    """One line per field of a JSON schema: its name, its type, what it means."""
    lines = []
    for name, prop in (schema.get("properties") or {}).items():
        # Optional fields arrive as anyOf [X, null]; describe the X.
        options = [p for p in prop.get("anyOf", [prop]) if p.get("type") != "null"]
        target = options[0] if options else prop
        nested = None
        ref = target.get("$ref") or (target.get("items") or {}).get("$ref")
        if ref:
            nested = defs.get(ref.rsplit("/", 1)[-1])
        kind = {
            "boolean": "true/false", "integer": "whole number", "number": "number",
            "string": "text", "array": "list", "object": "object",
        }.get(target.get("type", ""), "object" if nested else "value")
        described = prop.get("description") or target.get("description") or ""
        lines.append(f"{indent}- {name} ({kind}): {' '.join(described.split())}".rstrip(": "))
        if nested:
            lines.append(f"{indent}  Each entry in {name} has:")
            lines.extend(_describe(nested, defs, indent + "  "))
    return lines


def with_field_guide(instructions: str, output_type) -> str:
    """The instructions, plus what each output field means where it would be lost.

    Most of what the output fields mean is written in their descriptions:
    the reader's allowed values for objection_kind exist nowhere else, and the
    judge's "quote the candidate, not the reference answers" is there. OpenAI
    models receive those descriptions with the schema. DeepSeek-V4-Pro and
    Kimi-K2.7-Code on Azure do not -- asked for a field described as "always
    the number 42", both answer 4 with a random colour, while grok-4.6 answers
    42 and "purple" -- so they fill in named fields with no idea what the
    names are meant to mean. Graded that way, DeepSeek read one known pair in
    eleven correctly.

    So on Azure the descriptions are also written into the instructions, for
    every model there alike, so judges being compared see identical text. On
    the direct API this returns the instructions untouched. ERRATA_FIELD_GUIDE
    set to 1 or 0 forces it either way.
    """
    forced = os.environ.get("ERRATA_FIELD_GUIDE")
    wanted = (
        forced == "1"
        if forced in ("0", "1")
        else os.environ.get("ERRATA_PROVIDER", "").lower() == "azure"
    )
    if not wanted:
        return instructions
    schema = output_type.model_json_schema()
    guide = "\n".join(_describe(schema, schema.get("$defs", {})))
    return (
        f"{instructions}\n\n"
        f"Your reply is a JSON object with the fields below. What each one means:\n\n{guide}"
    )



# The exceptions a connection that failed raises, by name: the SDK's own and
# the transport's beneath it.
_DROPPED = frozenset({"APITimeoutError", "APIConnectionError", "InternalServerError", "TimeoutError",
                      "ConnectError", "ConnectTimeout", "ReadTimeout", "ReadError", "RemoteProtocolError"})


async def resilient(make_call, *, attempts: int = 4, pause: float = 60.0):
    """Run a model call, retrying the empty answer a throttled endpoint gives.

    Azure replies to a throttled deployment with HTTP 200 and an empty
    ``choices`` array rather than a 429, and the SDK raises
    ModelBehaviorError("ChatCompletion response has no choices"). Recorded, that
    becomes a permanent error on a row that was never really attempted: one
    regrade lost all thirty-six of its grading calls in seven seconds, while
    the same call answered in 180 seconds when made alone a few minutes later,
    and four at once answered 4/4.

    So it is retried here rather than a run later. Other failures are raised
    untouched -- a real error should stop being mistaken for a transient one.
    """
    import asyncio

    last = None
    for attempt in range(attempts):
        try:
            return await make_call()
        except Exception as e:  # noqa: BLE001 - re-raised below unless transient
            # A refused Claude call reaches here as a connection error; it is
            # not one, and waiting will not change the answer.
            if _refused(e):
                raise _refused(e) from e
            message = str(e).lower()
            # Two transient answers from a busy endpoint: an empty 200, which
            # is how Azure signals throttling, and a plain 429, which survived
            # the client's own retries when three processes shared one
            # deployment's 50,000 tokens a minute. And a connection that failed
            # rather than a call: a laptop that slept, a Wi-Fi drop, a gateway
            # timing out. After a sleep every call in flight fails within
            # seconds, and a stage reported itself finished with its rows in
            # error until someone ran it again (B-251).
            throttled = "no choices" in message or "429" in message or "rate limit" in message
            dropped = (type(e).__name__ in _DROPPED
                       or re.search(r"timed out|connection (?:error|reset|refused)|\b50[234]\b"
                                    r"|bad gateway|service unavailable", message) is not None)
            if not throttled and not dropped:
                raise
            last = e
            if attempt + 1 < attempts:
                # Spread out, not in lockstep. A throttled deployment rejects
                # every call in flight at once, so an exact delay sends them all
                # back together and they are throttled together again -- and
                # with ten grading calls at a time that is ten simultaneous
                # retries, three times over, before any of them gives up.
                import random

                await asyncio.sleep(pause * (attempt + 1) * random.uniform(0.6, 1.4))
    raise last

