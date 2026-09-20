import random

from urbanflow.behavior import Activity, ActivityPlan, PlanningContext


class GravityBaseline:
    """Seeded activity/time profile; attraction / (1 + walking_minutes)^1.5."""

    async def plan(self, context: PlanningContext) -> ActivityPlan:
        rng = random.Random(context.seed)
        agent = context.agent
        activities = [Activity(destination_id=agent.home_id, purpose="home", minute=0)]
        valid = {c.id for c in context.candidates}
        workday = context.day.weekday() < 5
        if (
            workday
            and agent.anchor_id is not None
            and agent.anchor_id in valid
            and agent.role in {"worker", "student"}
        ):
            activities.append(
                Activity(
                    destination_id=agent.anchor_id,
                    purpose="work" if agent.role == "worker" else "study",
                    minute=8 * 60 + rng.randrange(60),
                )
            )
        options = [c for c in context.candidates if c.id not in {agent.home_id, agent.anchor_id}]
        if options:
            for hour, purpose, preferred in (
                (17 if workday else 12, "food", ("amenity:cafe", "amenity:restaurant")),
                (19 if workday else 16, "shopping", ("shop:",)),
            ):
                weights = [
                    (4 if (c.category or "").startswith(preferred) else 1)
                    / (1 + c.walking_minutes) ** 1.5
                    for c in options
                ]
                destination = rng.choices(options, weights)[0]
                activities.append(
                    Activity(
                        destination_id=destination.id,
                        purpose=purpose,
                        minute=hour * 60 + rng.randrange(45),
                    )
                )
        activities.append(Activity(destination_id=agent.home_id, purpose="home", minute=21 * 60))
        return ActivityPlan(activities=activities)
