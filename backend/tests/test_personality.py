from mii.models import Resident
from mii.personality import JOBS, TRAITS, build_system_prompt, level


def make_resident(**overrides) -> Resident:
    fields = dict(id="r", island_id="i", name="Pat", job="coder", catchphrase="")
    fields.update(overrides)
    return Resident(**fields)


def test_level_buckets():
    assert level(0.0) == 0
    assert level(0.2) == 0
    assert level(0.5) == 1
    assert level(0.7) == 2
    assert level(1.0) == 2


def test_level_clamps_out_of_range_values():
    assert level(-3) == 0
    assert level(42) == 2


def test_prompt_includes_name_and_job():
    prompt = build_system_prompt(make_resident(job="reviewer"))
    assert "Pat" in prompt
    assert JOBS["reviewer"] in prompt


def test_unknown_job_gets_default_description():
    prompt = build_system_prompt(make_resident(job="lighthouse keeper"))
    assert "helpful member of a small software team" in prompt


def test_each_slider_changes_the_prompt():
    for trait, (low, _medium, high) in TRAITS.items():
        low_prompt = build_system_prompt(make_resident(**{trait: 0.0}))
        high_prompt = build_system_prompt(make_resident(**{trait: 1.0}))
        assert low in low_prompt and high not in low_prompt, trait
        assert high in high_prompt and low not in high_prompt, trait


def test_catchphrase_only_when_set():
    assert "catchphrase" not in build_system_prompt(make_resident())
    assert '"Ship it!"' in build_system_prompt(make_resident(catchphrase="Ship it!"))


def test_prompt_always_includes_the_worktree_rule():
    assert "Only work inside the current working directory" in build_system_prompt(make_resident())
