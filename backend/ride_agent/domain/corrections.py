"""Conservative discourse operators grounded in existing rider assertions.

Candidates are proposals, not facts. A revision must identify a unique field or
old value in source-backed rider history. Unknown/ambiguous references require
clarification. No benchmark values, payment authority, or model calls are involved.
"""

import re
from dataclasses import dataclass

REVISION = re.compile(
    (
        "\\b(?:correction|corrected|actually|instead|superseded|replac"
        "e|updated|i meant|make that|scratch that|on second "
        "thought|rather than|not .+? "
        "but)\\b"
    ),
    re.I,
)
REPORTED = re.compile("\\b(?:driver|agent|friend|someone|they)\\s+(?:said|suggested|told|wrote)\\b", re.I)
HYPOTHETICAL = re.compile(
    "(?:^\\s*if\\b|\\b(?:what if|suppose|hypothetically|if we|if i|for example)\\b)",
    re.I,
)


def clean(value):
    value = value.strip().strip("\"'“”‘’ ,;:.!?")
    value = re.sub(
        "^(?:it\\s+(?:was|is)|the\\s+(?:correct|right)\\s+\\w+\\s+(?:was|is))\\s+",
        "",
        value,
        flags=re.I,
    )
    return value.strip()


def tokens(value):
    return set(re.findall("[a-z0-9]+", value.lower())) - {
        "my",
        "our",
        "the",
        "current",
        "correct",
        "corrected",
        "previous",
        "original",
        "new",
    }


@dataclass
class Revision:
    field: str
    old: str
    new: str
    operator: str


def nonassertive(text):
    return bool(
        REPORTED.search(text)
        or HYPOTHETICAL.search(text)
        or re.search(
            ("\\b(?:don't|do not|never)\\s+(?:(?:want|need)\\s+to\\s+)?(?:change|replace|correct|update)\\b"),
            text,
            re.I,
        )
    )


def resolve(text, facts, events, declarations, pending=False):
    """Return grounded revisions, whether revision was attempted, and a reason."""
    text = text.replace("’", "'")
    active = [f for f in facts if f["active"]]
    if nonassertive(text):
        return ([], False, None)
    explicit_request = bool(
        re.search(
            (
                "^\\s*(?:correction\\s*[:,]|(?:actually[, "
                "]+)?(?:that|it)\\s+(?:was|is)\\s+(?:wrong|incorrect)|(?:please"
                "\\s+)?(?:replace|change|correct|update)\\s+(?:it|that|this)\\b)"
            ),
            text,
            re.I,
        )
    )
    attempted = explicit_request
    revisions = []
    candidates = []
    patterns = [
        (
            "typo_intended",
            "\\b(.+?)\\s+(?:was|is)\\s+(?:a\\s+)?(?:typo|wrong|incorrect);\\s*(.+?)\\s+(?:is|was)\\s+what\\s+i\\s+(?:meant|intended)(?=[.!?](?:\\s|$)|[;\\n]|$)",
            False,
        ),
        (
            "replace",
            "\\breplace\\s+(.+?)\\s+with\\s+(.+?)(?=[.!?](?:\\s|$)|[;\\n]|$)",
            False,
        ),
        (
            "from_to",
            "\\bfrom\\s+(.+?)\\s+to\\s+(.+?)(?=[.!?](?:\\s|$)|[;\\n]|$)",
            False,
        ),
        (
            "not_but",
            "\\b(?:not|wasn't|isn't)\\s+(.+?)(?:,?\\s+but\\s+|;\\s*(?:it\\s+)?(?:was|is)\\s+)(.+?)(?=[.!?](?:\\s|$)|[;\\n]|$)",
            False,
        ),
        (
            "reverse_not",
            ("\\b(?:i meant|make that|use|no,)\\s+(.+?),\\s*not\\s+(.+?)(?=[.!?](?:\\s|$)|[;\\n]|$)"),
            True,
        ),
        (
            "instead",
            ("\\b(.+?)\\s+(?:instead of|rather than)\\s+(.+?)(?=[.!?](?:\\s|$)|[;\\n]|$)"),
            True,
        ),
    ]
    for operator, pattern, reverse in patterns:
        if operator == "from_to" and (not re.search("\\b(?:change|switch|update|correct|replace)\\b", text, re.I)):
            continue
        for match in re.finditer(pattern, text, re.I):
            old, new = (clean(match[2]), clean(match[1])) if reverse else (clean(match[1]), clean(match[2]))
            new = clean(
                re.sub(
                    "^(?:please use|use|i meant|make that|actually|no,)\\s+",
                    "",
                    new,
                    flags=re.I,
                )
            )
            if re.fullmatch("\\$\\d+(?:\\.\\d{1,2})?", old):
                amounts = re.findall("\\$\\d+(?:\\.\\d{1,2})?", new)
                if len(amounts) == 1:
                    new = amounts[0]
            if old and new and (old.casefold() != new.casefold()):
                candidates.append((old, new, operator))
    attempted = attempted or bool(candidates)
    edges = {}
    for old, new, operator in candidates:
        named = [f for f in active if re.search("(?<!\\w)" + re.escape(f["value"]) + "(?!\\w)", old, re.I)]
        if len({(f["field"], f["value"]) for f in named}) == 1:
            old = named[0]["value"]
        if old.lower() in {"it", "that", "this", "that one", "that value", "that detail"} and active:
            latest = max((f["source"] for f in active))
            recent = [f for f in active if f["source"] == latest]
            if len(recent) == 1:
                old = recent[0]["value"]
        edges.setdefault(old.casefold(), []).append((new, operator))
    for old_key in edges:
        fields = {f["field"] for f in active if f["value"].casefold() == old_key}
        if len(fields) > 1:
            return ([], True, "The old value belongs to more than one rider field.")
    grounded = set()
    for fact in active:
        current = fact["value"]
        visited = set()
        while current.casefold() in edges:
            key = current.casefold()
            replacements = {new.casefold(): (new, operator) for new, operator in edges[key]}
            if len(replacements) != 1 or key in visited:
                return ([], True, "The replacement chain branches or contains a cycle.")
            visited.add(key)
            grounded.add(key)
            new, operator = next(iter(replacements.values()))
            revisions.append(Revision(fact["field"], current, new, operator))
            current = new
    if set(edges) - grounded:
        return ([], True, "A replacement does not match an earlier rider claim.")
    negated = []
    for f in active:
        if re.search(
            "\\b(?:not|wasn't|isn't)\\s+[\\\"']?" + re.escape(f["value"]) + "[\\\"']?(?!\\w)",
            text,
            re.I,
        ):
            negated.append(f)
    positive = [
        (field, clean(value))
        for field, value in declarations
        if not re.match("(?:not\\b|wasn't\\b|isn't\\b)", value, re.I)
    ]
    attempted = attempted or bool(negated) or bool(positive and (REVISION.search(text) or pending))
    for field, new in positive:
        if revisions and field in {r.field for r in revisions}:
            continue
        target = [f for f in active if tokens(field) == tokens(f["field"])]
        if not target:
            target = [
                f
                for f in active
                if tokens(field) and (tokens(field) <= tokens(f["field"]) or tokens(f["field"]) <= tokens(field))
            ]
        fields = {f["field"] for f in target}
        if attempted and len(fields) == 1:
            for f in target:
                if f["value"].casefold() != new.casefold():
                    revisions.append(Revision(f["field"], f["value"], new, "field_revision"))
        elif attempted and len(fields) > 1:
            return ([], True, "The corrected field matches more than one rider detail.")
    if negated and (not revisions):
        if len({f["field"] for f in negated}) == 1 and len(positive) == 1:
            revisions.append(
                Revision(
                    negated[0]["field"],
                    negated[0]["value"],
                    positive[0][1],
                    "negated_then_declared",
                )
            )
    match = re.search(
        ("\\b(?:i meant|make that|scratch that[,;:]?|on second thought[,;:]?)\\s+(.+?)(?=[.!?](?:\\s|$)|[;\\n]|$)"),
        text,
        re.I,
    )
    if match:
        attempted = True
        if not revisions and (not positive) and active:
            latest = max((f["source"] for f in active))
            recent = [f for f in active if f["source"] == latest]
            if len(recent) == 1 and clean(match[1]):
                revisions.append(
                    Revision(
                        recent[0]["field"],
                        recent[0]["value"],
                        clean(match[1]),
                        "unique_recent_referent",
                    )
                )
    unique = {(r.field, r.old, r.new): r for r in revisions}
    revisions = list(unique.values())
    reason = (
        ("The correction does not identify a unique earlier rider detail and replacement.")
        if attempted and (not revisions) and (not positive)
        else None
    )
    return (revisions, attempted, reason)
