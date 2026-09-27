"""errata-bench's agents for Harbor (github.com/laude-institute/harbor).

Kept apart from `errata_bench`, which never imports them, and run in Harbor's
own environment, not errata-bench's: Harbor 0.23.0 needs litellm, which needs
openai below 3, and errata-bench's lock pins openai 3.13.0, so the two cannot
share one environment (checked 09-27). None is needed: these agents use only
modules of `errata_bench` that import nothing outside the standard library,
and the reference agent's loop runs inside the task's container, in an
environment of its own built from errata-bench's lock. So in Harbor's
environment errata-bench is installed without its dependencies:

    pip install harbor==0.23.0 && pip install --no-deps -e <errata-bench>
"""
