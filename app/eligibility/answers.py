"""Validate typed answers without treating omitted, null, no and zero alike."""

import re
from datetime import date

from pydantic import model_validator

from app.common.errors import AppError
from app.common.models import Model


class PriorStay(Model):
    starts_on: date
    ends_on: date

    @model_validator(mode="after")
    def ordered(self):
        if self.ends_on < self.starts_on:
            raise ValueError("Previous stay ends before it starts")
        return self


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Expected YYYY-MM-DD")
    return date.fromisoformat(value)


def validate_answers(answers, questions):
    invalid = set(answers) - questions.keys()
    result = {}
    for key, value in answers.items():
        if key in invalid:
            continue
        question = questions[key]
        if value is None:
            result[key] = None
            continue
        try:
            if question.value_type == "boolean":
                if type(value) is not bool:
                    raise ValueError("Expected boolean")
                result[key] = value
            elif question.value_type == "integer":
                if type(value) is not int:
                    raise ValueError("Expected integer")
                if question.minimum is not None and value < question.minimum:
                    raise ValueError("Below minimum")
                if question.maximum is not None and value > question.maximum:
                    raise ValueError("Above maximum")
                result[key] = value
            elif question.value_type == "date":
                result[key] = parse_date(value)
            elif question.value_type == "enum":
                if not isinstance(value, str) or value not in question.choices:
                    raise ValueError("Unknown choice")
                result[key] = value
            elif question.value_type == "stays":
                if not isinstance(value, list) or len(value) > 50:
                    raise ValueError("Expected up to 50 previous stays")
                result[key] = []
                for interval in value:
                    if not isinstance(interval, dict) or set(interval) != {"starts_on", "ends_on"}:
                        raise ValueError("Expected a date interval")
                    result[key].append(PriorStay(
                        starts_on=parse_date(interval["starts_on"]), ends_on=parse_date(interval["ends_on"]),
                    ))
        except (ValueError, TypeError):
            invalid.add(key)
    if invalid:
        raise AppError(422, "invalid_answers", "Проверьте типы и ключи дополнительных ответов.", {
            "fields": [f"answers.{key}" for key in sorted(invalid)],
        })
    return result


def answers_for_current_schema(saved_answers, questions):
    """Read older persisted answers without losing the original JSON on disk.

    New HTTP writes still use strict validate_answers. Retired or changed
    questions require review; an old value cannot silently prove a new rule.
    """
    compatible, changed = {}, []
    for key, value in saved_answers.items():
        if key not in questions:
            changed.append("answers." + key)
            continue
        try:
            validate_answers({key: value}, questions)
            compatible[key] = value
        except AppError:
            compatible[key] = None
            changed.append("answers." + key)
    return compatible, sorted(changed)
