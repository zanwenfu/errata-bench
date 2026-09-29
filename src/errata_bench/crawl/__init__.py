"""A second source of tasks: agent sessions recorded with Entire, collected from public repositories (#16).

SWE-chat's sessions came from public repositories whose developers record
their agent sessions with Entire's CLI, and those repositories kept recording
after SWE-chat's last session (19 April 2026). This package collects them and
writes them in the shape of SWE-chat's tables, so the rest of the pipeline
reads them as it reads SWE-chat.

The stages, each resumable, each writing its own files under one output
directory (``data/entire/`` by default, which git ignores):

1. ``discover``: GitHub's commit search for the ``Entire-Checkpoint`` trailer,
   sliced by date, then each repository's metadata. Copies of a commit in
   several repositories are merged into one owner.
2. ``select``: the repositories to collect, by licence, and the reasons the
   others are left out.
3. ``fetch``: for each selected repository, anonymously, only Entire's own refs
   (the v1 branch, per-checkpoint refs, or a public separate checkpoint
   repository), the metadata of every checkpoint, and each session's most
   complete transcript.

Where Entire keeps a session, and why each choice here was made, is in
``entire.py``. Nothing collected is ever committed: transcripts hold what
developers typed.
"""
