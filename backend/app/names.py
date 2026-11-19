"""Names as callers spell them on the phone.

Callers are asked to spell names letter by letter, and the assistant sometimes passes the
letters on as they were heard, "T A K W A" or "T-A-K-W-A". Three or more single letters in a
row are joined into one word, so staff see "Takwa". Ordinary names and initials ("A J Smith")
are left alone.
"""

import re

# Letters split by the same separator throughout: one space, or a dot, dash or comma with an
# optional space. A different separator ends the word, so "T A K W A  S U I S S I" and
# "T-A-K-W-A S-U-I-S-S-I" are each two names.
SPELLED_RUN = re.compile(
    r"(?<![A-Za-z])[A-Za-z]( ?[.\-,] ?| )[A-Za-z](?:\1[A-Za-z](?![A-Za-z]))+(?![A-Za-z])[.,]?"
)


def join_spelled(name: str) -> str:
    def word(match: re.Match) -> str:
        letters = re.sub(r"[^A-Za-z]", "", match.group(0))
        return letters[0].upper() + letters[1:].lower()

    return " ".join(SPELLED_RUN.sub(word, name).split())


def clean_name(value):
    """A Pydantic validator: spelled names joined, other values passed through."""
    return join_spelled(value) if isinstance(value, str) else value
