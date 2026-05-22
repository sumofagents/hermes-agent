import re

from agent.recall_gate import (
    RISK_MANDATORY,
    RISK_NO_RECALL,
    build_queries,
    classify_risk,
    render_degraded_notice,
    sanitize_for_query,
)


def test_job_application_prompt_triggers_mandatory_recall():
    risk = classify_risk("Help me fill out this job application work authorization section")
    assert risk.level == RISK_MANDATORY
    assert risk.mandatory is True
    assert "job_application" in risk.risk_classes


def test_anduril_spacex_prompt_triggers_job_and_continuity_labels():
    risk = classify_risk("help me fill this Anduril application using what you know from the SpaceX application")
    assert risk.level == RISK_MANDATORY
    assert risk.mandatory is True
    assert "job_application" in risk.risk_classes
    assert "continuity" in risk.risk_classes
    assert "job_application" in risk.labels
    assert "continuity" in risk.labels


def test_same_as_before_triggers_mandatory_without_job_keywords():
    risk = classify_risk("same as before")
    assert risk.level == RISK_MANDATORY
    assert risk.mandatory is True
    assert risk.risk_classes == ("continuity",)


def test_arithmetic_and_generic_coding_do_not_trigger_recall():
    assert classify_risk("what is 19 * 23?").level == RISK_NO_RECALL
    assert classify_risk("write a python function that reverses a list").level == RISK_NO_RECALL
    assert classify_risk("debug this generic TypeScript type error").mandatory is False


def test_plural_job_application_classes_trigger_mandatory_recall():
    for prompt in [
        "job applications",
        "employment forms",
        "employment applications",
        "cover letters",
        "resumes",
        "help me complete employment forms",
        "help me draft cover letters",
    ]:
        risk = classify_risk(prompt)
        assert risk.level == RISK_MANDATORY, prompt
        assert "job_application" in risk.risk_classes, prompt


def test_application_false_positive_battery_requires_employment_context():
    assert classify_risk("build a todo application in React").level == RISK_NO_RECALL
    assert classify_risk("open the Hermes application logs").level == RISK_NO_RECALL
    assert classify_risk("debug the background job failure in Python").level == RISK_NO_RECALL
    assert classify_risk("write a cron job that cleans temp files").level == RISK_NO_RECALL
    assert classify_risk("what is the latest SpaceX launch?").level == RISK_NO_RECALL
    assert classify_risk("the application does not work").level == RISK_NO_RECALL
    assert classify_risk("why doesn't my application work?").level == RISK_NO_RECALL
    assert classify_risk("work on the todo application").level == RISK_NO_RECALL
    assert classify_risk("get the web application to work properly").level == RISK_NO_RECALL


def test_query_builder_is_deterministic_sanitized_and_class_expanded():
    msg = "help me fill this Anduril application\x00 using what you know from SpaceX"
    risk = classify_risk(msg)
    queries = build_queries(msg, risk)
    assert queries[0] == "help me fill this Anduril application using what you know from SpaceX"
    assert all("\x00" not in q for q in queries)
    assert any("job application resume work authorization" in q for q in queries)
    assert any("same as before previous answer" in q for q in queries)
    assert len(queries) == len(set(queries))
    assert len(queries) <= 4


def test_sanitize_for_query_strips_control_chars_and_collapses_space():
    assert sanitize_for_query("a\x00\n\t b") == "a b"


def test_degraded_notice_is_fenced_explicit_and_within_budget():
    risk = classify_risk("same as before")
    notice = render_degraded_notice(risk)
    assert "## Enforced Memory Recall" in notice
    assert "mandatory=true" in notice
    assert "stored memory could not be reached" in notice.lower()
    assert len(notice) <= 3500
    assert re.search(r"Reason: .*continuity", notice)
