"""A later event never completes earlier stages or invents an outcome."""

from app.common.models import Event, Outcome, Process, ProcessStage, ProcessStageTemplate
from app.eligibility.conditions import evaluate_condition


def project_process(
    events: list[Event], process_template: list[ProcessStageTemplate], *,
    context=None, unverified_source_ids=(),
) -> Process:
    context = context or {"profile": {}, "trip": {}, "answers": {}}
    stages = []
    for template in process_template:
        applicability = evaluate_condition(template.applies_if, context).value
        if set(template.source_ids) & set(unverified_source_ids) and applicability != "no":
            applicability = "unknown"
        matching = [event.id for event in events if event.event_type in template.event_types]
        stages.append(ProcessStage(
            id=template.id, title=template.title, applicability=applicability,
            completed=applicability == "yes" and bool(matching), event_ids=matching,
        ))
    applicable = [stage for stage in stages if stage.applicability == "yes"]
    unknown = any(stage.applicability == "unknown" for stage in stages)
    percent = 100 * sum(stage.completed for stage in applicable) // len(applicable) if applicable and not unknown else None
    event_types = {kind for stage in process_template for kind in stage.event_types}
    visa_free = bool(event_types) and event_types <= {"entry_preparation_completed"}
    outcome = Outcome.NOT_APPLICABLE if visa_free else Outcome.UNKNOWN
    if not visa_free:
        decisions = sorted(
            (event for event in events if event.event_type == "decision_received"
             and event.outcome in (Outcome.APPROVED, Outcome.REFUSED)),
            key=lambda event: (event.occurred_at, event.received_at, str(event.id)),
        )
        if decisions:
            outcome = decisions[-1].outcome
    current = next((stage.id for stage in applicable if not stage.completed), None)
    return Process(percent=percent, stages=stages, current_stage=current, outcome=outcome)


class EventProjector:
    def project_process(self, events, process_template, **kwargs) -> Process:
        return project_process(events, process_template, **kwargs)
