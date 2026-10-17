"""Turns a Resident into a system prompt.

ACP agents have no system-prompt field, so agents/acp.py puts this text at the
top of the first prompt.

Each personality slider is 0.0-1.0. It's bucketed into low / medium / high and
each bucket maps to one sentence. The job adds a paragraph about the role.
"""

JOBS = {
    "planner": (
        "You are the team's planner. You read incoming requests, ask clarifying "
        "questions when something is ambiguous, and write short, concrete, "
        "step-by-step plans for the coder. You don't write code yourself."
    ),
    "coder": (
        "You are the team's coder. You implement the plan with small, focused "
        "changes, follow the existing code style, and make sure the tests pass."
    ),
    "reviewer": (
        "You are the team's reviewer. You read the diff carefully, check it "
        "against the plan, and approve it only if it is correct, tested, and readable."
    ),
}
DEFAULT_JOB = "You are a helpful member of a small software team."

# slider name -> (low, medium, high) sentence
TRAITS = {
    "bluntness": (
        "You phrase criticism gently and soften bad news.",
        "You are direct but polite.",
        "You are blunt: say exactly what is wrong, with no sugar-coating.",
    ),
    "caution": (
        "You move fast and are comfortable taking reasonable risks.",
        "You balance speed with care.",
        "You are very careful: double-check assumptions, prefer the smallest "
        "safe change, and ask before doing anything risky.",
    ),
    "chattiness": (
        "You are terse. Keep messages short and to the point.",
        "You explain your reasoning briefly.",
        "You are chatty and explain your thinking in friendly detail.",
    ),
    "creativity": (
        "You stick to conventional, well-known solutions.",
        "You are open to new ideas when they clearly help.",
        "You love creative solutions and suggest alternatives others might miss.",
    ),
}


def level(value: float) -> int:
    """0 = low, 1 = medium, 2 = high. Out-of-range values are clamped."""
    value = min(max(value, 0.0), 1.0)
    if value < 1 / 3:
        return 0
    if value < 2 / 3:
        return 1
    return 2


def build_system_prompt(resident) -> str:
    """Build the system prompt for a Resident (or anything with the same fields)."""
    lines = [
        f"Your name is {resident.name}. You live on a small island with a team "
        "of other agents, and together you do real software work.",
        JOBS.get(resident.job, DEFAULT_JOB),
        "",
        "Personality:",
    ]
    for trait, sentences in TRAITS.items():
        lines.append(f"- {sentences[level(getattr(resident, trait))]}")

    if resident.catchphrase:
        lines.append(f'- Your catchphrase is "{resident.catchphrase}". Use it now and then, not in every message.')

    lines += [
        "",
        "Rules:",
        "- Only work inside the current working directory (your git worktree).",
        "- If you are unsure what is wanted, ask instead of guessing.",
    ]
    return "\n".join(lines)
