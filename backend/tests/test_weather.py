from datetime import timedelta

from conftest import ScriptedRunner, db_test, make_workflow

from mii import weather
from mii.models import Resident, Step, utcnow

pytestmark = db_test


def test_weather_from_outcomes():
    assert weather.from_outcomes([]) == "sunny"
    assert weather.from_outcomes(["pass"] * 8 + ["fail"] * 2) == "sunny"
    assert weather.from_outcomes(["pass", "fail"]) == "cloudy"
    assert weather.from_outcomes(["fail", "fail", "pass"]) == "rainy"


async def test_current_reads_recent_steps_and_sleepers(island):
    assert await weather.current("sunny") == "sunny"  # nothing happened yet
    t0 = utcnow() - timedelta(minutes=5)
    for i, outcome in enumerate(["pass"] * 3 + ["fail"] * 6):
        await Step.objects.acreate(island_id="sunny", task_id="t_x", building_id="lab", building_type="lab",
                                   resident_id="carlos", brain="mock", outcome=outcome,
                                   started_at=t0, finished_at=t0 + timedelta(seconds=i))
    assert await weather.current("sunny") == "rainy"
    await Step.objects.acreate(island_id="sunny", task_id="t_y", building_id="lab", building_type="lab",
                               resident_id="carlos", brain="mock", outcome="")  # still running: ignored
    assert await weather.current("sunny") == "rainy"
    await Resident.objects.filter(island_id="sunny").aupdate(state="sleeping")
    assert await weather.current("sunny") == "night"
    assert await weather.current("nowhere") == "sunny"  # an island with nobody on it


async def test_engine_announces_weather_changes(island):
    workflow, client = make_workflow(ScriptedRunner())
    task = await workflow.submit("sunny", "Hi")
    await workflow.run_task(task.id)
    announced = [e["weather"] for e in client.of_type("weather")]
    assert announced == ["sunny"]  # said once, not after every step
    await workflow._put_idle_residents_to_sleep()
    assert [e["weather"] for e in client.of_type("weather")] == ["sunny", "night"]
    # The last activity line is kept on the resident for late joiners.
    await workflow._report(task, await Resident.objects.aget(pk="carlos"), "Reading auth.py")
    assert (await Resident.objects.aget(pk="carlos")).activity == "Reading auth.py"
