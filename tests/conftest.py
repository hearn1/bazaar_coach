"""
tests/conftest.py — pytest configuration and test inventory.

Test categories
---------------

legacy_log  (marked with @pytest.mark.legacy_log)
    Tests that drive watcher.py / parser.py directly with synthetic log lines.
    Safe to delete once those modules are removed in #136.

    test_watcher_completion_legacy.py

Mono-only
    Tests that exercise MonoEventAdapter in isolation (using a _Collector stub,
    not a real RunState).  No log dependency.

    test_mono_event_adapter_basic.py
    test_mono_event_adapter_purchase.py
    test_mono_event_adapter_reroll.py
    test_mono_event_adapter_sell.py
    test_mono_event_adapter_combat_outcome.py

Mono end-to-end
    Tests that feed MonoEventAdapter → RunState → real SQLite DB.
    No log dependency.

    test_mono_adapter_end_to_end.py
    test_run_init_synthetic_ids.py

Log-agnostic (RunState event-dict API)
    Tests that call RunState.process(event) directly; no watcher/parser in the
    loop.  Survive removal of watcher.py / parser.py.

    test_run_state_completion.py
    test_run_state_live_context.py
    test_run_state_force_end.py
    test_run_state_offer_snapshot_attach.py
    test_run_init_synthetic_ids.py
    test_event_source_dedup.py
    test_skill_template_resolve.py
    test_skill_template_resolve_missing_mono.py
    test_event_choice_template_resolve.py
    test_event_choice_template_resolve_missing_offer_snapshot.py
    test_live_scorer_context.py
    test_combat_results_opponent_board_link.py
    test_discord_alpha_fixes.py

Infrastructure / DB / overlay / scorer (no log dependency)
    test_migrations.py
    test_retention.py
    test_settings.py
    test_first_run.py
    test_server_round2.py
    test_server_force_end_route.py
    test_overlay_round2.py
    test_overlay_live_snapshot_scoping.py
    test_overlay_review_shop_buffer.py
    test_offer_snapshot_lookup.py
    test_review_builder_missed_items.py
    test_api_runs_perf.py
    test_build_helpers_round1.py
    test_builds_schema.py
    test_catalog_coverage.py
    test_catalog_dashboard_round2.py
    test_dashboard_my_builds.py
    test_score_phase_universal_items.py
    test_timing_weight_curves.py
    test_tracker_round2.py
    test_update_checker.py
    test_user_builds_api.py
    test_user_builds_resolver.py
    test_refresh_builds.py
    test_refresh_content.py
    test_refresh_images.py
    test_packaging.py
"""
