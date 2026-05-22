from agent.retrieval_router import (
    ComplexityTier,
    RouteKind,
    build_deterministic_plan,
    merge_llm_refinement,
    render_route_context,
)


def route_kinds(plan):
    return [route.kind for route in plan.routes]


def test_anduril_spacex_prompt_routes_memory_and_sessions_without_web():
    plan = build_deterministic_plan(
        "help me fill this Anduril application using what you know from the SpaceX application"
    )

    assert plan.enabled is True
    assert plan.mandatory is True
    assert "job_application" in plan.labels
    assert "continuity" in plan.labels
    assert RouteKind.MEMORY_SEMANTIC in route_kinds(plan)
    assert RouteKind.SESSION_SEMANTIC in route_kinds(plan)
    assert RouteKind.SESSION_FTS in route_kinds(plan)
    assert RouteKind.WEB_SEARCH not in route_kinds(plan)
    assert all(route.read_only for route in plan.routes)


def test_same_as_before_routes_memory_and_sessions():
    plan = build_deterministic_plan("same as before")

    assert plan.mandatory is True
    assert plan.labels == ("continuity",)
    assert RouteKind.MEMORY_SEMANTIC in route_kinds(plan)
    assert RouteKind.SESSION_SEMANTIC in route_kinds(plan)
    assert RouteKind.SESSION_FTS in route_kinds(plan)


def test_current_public_prompt_routes_web_without_private_memory():
    plan = build_deterministic_plan("tell me the latest public SpaceX launch news?")

    assert plan.mandatory is False
    assert plan.complexity_tier in {ComplexityTier.SIMPLE, ComplexityTier.STANDARD}
    assert route_kinds(plan) == [RouteKind.WEB_SEARCH]
    assert plan.routes[0].private_query is False
    assert "latest public SpaceX launch news" in plan.routes[0].queries[0]


def test_url_prompt_routes_web_extract():
    plan = build_deterministic_plan("summarize https://example.com/research")

    assert route_kinds(plan) == [RouteKind.WEB_EXTRACT]
    assert plan.routes[0].queries == ("https://example.com/research",)


def test_private_url_prompt_does_not_route_web_extract():
    plan = build_deterministic_plan("summarize my medical diagnosis at https://example.com/report")

    assert RouteKind.WEB_EXTRACT not in route_kinds(plan)
    assert plan.routes == ()


def test_token_or_profile_url_prompt_does_not_route_web_extract():
    for prompt in [
        "summarize https://example.com/?token=abc123",
        "summarize https://example.com/users/jeremiah/profile",
        "summarize https://example.com/?access_token=abc",
        "summarize https://example.com/?authToken=abc",
        "summarize https://example.com/?session_id=abc",
        "summarize https://example.com/?bearer_token=abc",
        "what is latest at https://example.com/?access_token=abc",
        "latest https://example.com/users/jeremiah/profile",
        "summarize https://abc:password@example.com/news",
        "what is latest at https://abc:password@example.com/news",
        "summarize https://abc:xyz@example.com/news",
        "what is latest at https://abc:xyz@example.com/news",
    ]:
        plan = build_deterministic_plan(prompt)
        assert RouteKind.WEB_EXTRACT not in route_kinds(plan), prompt
        assert RouteKind.WEB_SEARCH not in route_kinds(plan), prompt


def test_internal_or_private_network_url_prompt_does_not_route_web():
    for prompt in [
        "summarize http://127.0.0.1:8000/news",
        "summarize http://localhost:8000/news",
        "summarize http://10.0.0.1/news",
        "summarize http://172.16.0.5/news",
        "summarize http://192.168.1.10/news",
        "summarize http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "summarize https://internal.example.local/news",
        "what is latest at http://127.0.0.1:8000/news",
        "what is latest at https://internal.example.local/news",
        "summarize http://2130706433/news",
        "summarize http://0x7f000001/news",
        "summarize http://127.1/news",
        "summarize http://0x7f.0.0.1/news",
        "summarize http://0x7f.1/news",
        "summarize http://127.0x0.0.1/news",
        "summarize http://100.64.0.1/news",
        "summarize http://224.0.0.1/news",
        "summarize http://[ff02::1]/news",
        "summarize http://[fec0::1]/news",
        "summarize http://[::1]/news",
        "summarize http://[::ffff:127.0.0.1]/news",
        "summarize http://[fe80::1]/news",
    ]:
        plan = build_deterministic_plan(prompt)
        assert RouteKind.WEB_EXTRACT not in route_kinds(plan), prompt
        assert RouteKind.WEB_SEARCH not in route_kinds(plan), prompt


def test_public_url_with_file_path_terms_routes_web_extract_not_local_file():
    plan = build_deterministic_plan("summarize https://example.com/files/report")

    assert route_kinds(plan) == [RouteKind.WEB_EXTRACT]
    assert plan.routes[0].queries == ("https://example.com/files/report",)


def test_explicit_repo_file_prompt_routes_file_search_read_only():
    plan = build_deterministic_plan("search this repo for retrieval_router.py")

    assert route_kinds(plan) == [RouteKind.FILE_SEARCH]
    assert plan.routes[0].read_only is True
    assert plan.routes[0].private_query is True


def test_arithmetic_prompt_plans_no_routes():
    plan = build_deterministic_plan("what is 19 * 23?")

    assert plan.enabled is True
    assert plan.mandatory is False
    assert plan.routes == ()
    assert plan.reason == "no_retrieval_needed"


def test_private_profile_prompt_never_routes_to_web_even_with_latest_keyword():
    plan = build_deterministic_plan("use my profile and latest resume details for this job application")

    assert plan.mandatory is True
    assert RouteKind.MEMORY_SEMANTIC in route_kinds(plan)
    assert RouteKind.WEB_SEARCH not in route_kinds(plan)
    assert all(route.private_query for route in plan.routes)


def test_non_g2_private_freshness_prompt_does_not_route_web():
    plan = build_deterministic_plan("what is the latest about my medical diagnosis?")

    assert RouteKind.WEB_SEARCH not in route_kinds(plan)
    assert plan.reason == "no_retrieval_needed"


def test_job_relationship_freshness_prompt_does_not_route_web():
    plan = build_deterministic_plan("what is the latest news about the job I applied to at Anduril?")

    assert RouteKind.WEB_SEARCH not in route_kinds(plan)
    assert plan.reason == "no_retrieval_needed"


def test_repo_file_freshness_prompt_routes_file_only_not_web():
    plan = build_deterministic_plan("what is the latest in this repo about retrieval routing?")

    assert route_kinds(plan) == [RouteKind.FILE_SEARCH]
    assert plan.routes[0].private_query is True


def test_secretish_file_prompt_is_denied_at_planning_layer():
    for prompt in [
        "read file .env",
        "read file .env?",
        "read file .env,",
        "read file .env.local",
        "read file path=.env",
        "read file secrets/config.yaml",
        "read file ~/.aws/credentials",
        "read file .npmrc",
        "open file ~/.ssh/id_rsa.",
        "search this repo for api_key",
    ]:
        plan = build_deterministic_plan(prompt)
        assert plan.routes == (), prompt
        assert plan.reason == "no_retrieval_needed"


def test_file_read_descriptor_has_positive_read_only_path():
    plan = build_deterministic_plan("read file docs/memory/G3_CONTRACT.md")

    assert route_kinds(plan) == [RouteKind.FILE_READ]
    assert plan.routes[0].read_only is True
    assert plan.routes[0].private_query is True


def test_llm_refinement_cannot_add_disallowed_route_or_raise_budget():
    plan = build_deterministic_plan("same as before")
    refined = merge_llm_refinement(
        plan,
        {
            "char_budget": plan.char_budget + 5000,
            "routes": [
                {"kind": "web_search", "queries": ["same as before private user profile"]},
                {"kind": "memory_semantic", "queries": ["previous durable preference"]},
            ],
        },
    )

    assert refined.char_budget == plan.char_budget
    assert RouteKind.WEB_SEARCH not in route_kinds(refined)
    assert RouteKind.MEMORY_SEMANTIC in route_kinds(refined)
    assert any("previous durable preference" in route.queries for route in refined.routes)


def test_llm_refinement_cannot_replace_public_web_query_with_private_query():
    plan = build_deterministic_plan("what is the latest public SpaceX launch news?")
    refined = merge_llm_refinement(
        plan,
        {
            "routes": [
                {"kind": "web_search", "queries": ["latest about Jeremiah private medical diagnosis"]},
            ],
        },
    )

    assert refined.routes == plan.routes


def test_llm_refinement_cannot_replace_public_web_extract_with_private_url():
    plan = build_deterministic_plan("summarize https://example.com/research")
    refined = merge_llm_refinement(
        plan,
        {
            "routes": [
                {"kind": "web_extract", "queries": ["https://example.com/users/jeremiah/profile"]},
            ],
        },
    )

    assert refined.routes == plan.routes


def test_llm_refinement_cannot_replace_file_route_with_secret_file():
    plan = build_deterministic_plan("read file docs/memory/G3_CONTRACT.md")
    for query in [
        "read file .env",
        "read file .env.local",
        "read file path=.env",
        "read file secrets/config.yaml",
        "read file ~/.aws/credentials",
        "read file .npmrc",
    ]:
        refined = merge_llm_refinement(
            plan,
            {"routes": [{"kind": "file_read", "queries": [query]}]},
        )

        assert refined.routes == plan.routes, query


def test_disabled_plan_short_circuits_to_no_routes():
    plan = build_deterministic_plan("what is the latest public SpaceX launch news?", enabled=False)

    assert plan.enabled is False
    assert plan.routes == ()
    assert plan.reason == "disabled"


def test_render_route_context_deduplicates_and_respects_budget():
    block = render_route_context(
        [
            {"route": "memory_semantic", "id": "a", "content": "Durable fact", "score": 0.9},
            {"route": "session_fts", "id": "b", "content": "Durable fact", "score": 0.8},
            {"route": "web_search", "id": "c", "content": "Fresh public fact", "score": 0.7},
        ],
        char_budget=220,
    )

    assert block.startswith("## Unified Retrieval Context")
    assert block.count("Durable fact") == 1
    assert "Fresh public fact" in block
    assert len(block) <= 220
