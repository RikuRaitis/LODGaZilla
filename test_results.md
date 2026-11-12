# Test cases and durations

Full suite (`python -m unittest discover -s tests`), timed per test on 2026-11-11 09:07, Python 3.11.3, Windows 10.
Times include each test's own setUp and tearDown, but not class-level setup (`setUpClass`), so a module's total can be below its wall-clock time. Durations vary between runs and machines.

## Summary

- Tests: 616
- Results: 614 passed, 2 skipped
- Wall-clock time: 14.5 min (870.39 s); sum of test times: 866.91 s

## Slowest 25 tests

| # | Test | Seconds | Result |
| ---: | --- | ---: | --- |
| 1 | `test_workspace_file.WorkspaceMenuTests.test_save_open_and_recent_list_restore_navigator_and_cards` | 40.70 | passed |
| 2 | `test_review_app.MainWindowCloseTests.test_close_flushes_first_stops_plugins_and_disposes_every_card` | 26.27 | passed |
| 3 | `test_review_app.MainWindowCloseTests.test_settings_are_saved_when_a_teardown_step_raises` | 22.77 | passed |
| 4 | `test_gui_grouped_legends.GroupedLegendTests.test_axis_moves_between_grouped_cards_leave_source_empty` | 22.64 | passed |
| 5 | `test_video_attachments.VideoAttachmentTests.test_sync_updates_only_matching_video_and_refreshes_activation_offset` | 20.09 | passed |
| 6 | `test_plugin_updates.InstallationTests.test_local_commits_are_never_discarded` | 15.85 | passed |
| 7 | `test_plugin_updates.InstallationTests.test_update_preserves_settings_and_can_be_repeated` | 15.60 | passed |
| 8 | `test_review_navigator.BackgroundSnapshotTests.test_first_background_load_shows_loading_then_tree` | 14.24 | passed |
| 9 | `test_review_navigator.BackgroundSnapshotTests.test_edit_during_background_job_survives_the_job_result` | 13.97 | passed |
| 10 | `test_video_attachments.VideoAttachmentTests.test_video_context_menu_identifies_clicked_file` | 11.81 | passed |
| 11 | `test_review_navigator.BackgroundSnapshotTests.test_folder_switch_before_deferred_initial_load_wins` | 10.85 | passed |
| 12 | `test_plugin_contracts.CatalogTests.test_core_has_no_imports_from_installed_plugins` | 9.98 | passed |
| 13 | `test_review_navigator.NavigatorDataLayerTests.test_snapshot_job_imports_no_gui_modules` | 9.65 | passed |
| 14 | `test_plugin_updates.InstallationTests.test_incoming_file_cannot_overwrite_ignored_settings` | 9.26 | passed |
| 15 | `test_review_navigator.BackgroundSnapshotTests.test_metadata_edit_updates_index_without_rescanning` | 9.16 | passed |
| 16 | `test_process_tree.ProcessTreeTests.test_forced_parent_exit_cleans_pool_and_converter_descendants` | 9.16 | passed |
| 17 | `test_review_navigator.BackgroundSnapshotTests.test_background_refresh_reads_disk_off_the_gui_thread` | 9.00 | passed |
| 18 | `test_review_navigator.AddMetadataRowEditorTests.test_typed_value_in_add_metadata_row_is_saved` | 8.96 | passed |
| 19 | `tests.test_gui_grouped_legends.GroupedLegendTests.test_axis_moves_between_grouped_cards_leave_source_empty` | 8.82 | passed |
| 20 | `test_video_attachments.VideoAttachmentTests.test_sync_buttons_work_in_either_order_and_show_the_sync_marker_until_done` | 8.78 | passed |
| 21 | `test_plugin_updates.InstallationTests.test_edit_after_download_postpones_update` | 8.53 | passed |
| 22 | `test_video_attachments.VideoAttachmentTests.test_remove_link_preserves_file_other_links_and_other_cards` | 8.45 | passed |
| 23 | `test_review_navigator.AddMetadataRowEditorTests.test_unchanged_value_commit_does_not_write` | 8.43 | passed |
| 24 | `test_review_navigator.BackgroundSnapshotTests.test_deferred_initial_load_scans_in_the_background` | 7.63 | passed |
| 25 | `test_workspace_file.WorkspaceRestoreTests.test_cards_rows_views_colors_and_markers_survive_a_round_trip` | 7.62 | passed |

## Modules

| Module | Tests | Seconds |
| --- | ---: | ---: |
| `test_review_navigator` | 21 | 135.59 |
| `test_gui_grouped_legends` | 42 | 112.25 |
| `test_gui_navigator_widget` | 41 | 107.72 |
| `test_video_attachments` | 8 | 71.42 |
| `test_plugin_updates` | 16 | 64.93 |
| `test_workspace_file` | 12 | 55.36 |
| `test_review_app` | 19 | 55.12 |
| `test_image_attachments` | 9 | 30.45 |
| `test_gui_data_drop` | 22 | 26.95 |
| `test_gui_plot_policy` | 22 | 21.56 |
| `test_audio_media` | 11 | 19.59 |
| `test_frequency_plot` | 21 | 15.36 |
| `test_plugin_contracts` | 14 | 12.90 |
| `test_gui_multi_plot_widget` | 17 | 11.98 |
| `test_demo_data_app` | 11 | 11.76 |
| `test_video_decoder` | 13 | 10.50 |
| `test_process_tree` | 2 | 9.17 |
| `test_core_startup` | 3 | 7.75 |
| `test_review_plots` | 28 | 7.11 |
| `test_multi_series_lanes` | 13 | 6.33 |
| `test_xyz_plot` | 13 | 6.27 |
| `test_filmstrip` | 9 | 6.11 |
| `test_demo_dataset` | 8 | 5.01 |
| `test_gui_plot_rows` | 11 | 4.97 |
| `test_gui_lodgazilla` | 17 | 4.83 |
| `test_review_gnss_xy` | 18 | 4.75 |
| `test_gui_gnss_3d_plot_widget` | 12 | 4.64 |
| `test_plot_theme` | 4 | 4.20 |
| `test_time_offset` | 8 | 4.05 |
| `test_app_settings` | 16 | 3.86 |
| `test_card_visibility` | 5 | 3.64 |
| `test_value_boxes` | 6 | 3.32 |
| `test_selection_duration` | 5 | 3.11 |
| `test_point_markers` | 6 | 2.93 |
| `test_folder_info_transactions` | 6 | 2.87 |
| `test_import_helpers` | 17 | 1.62 |
| `test_sample_limit` | 12 | 1.32 |
| `test_gui_gnss_plot_widget` | 7 | 0.90 |
| `test_plugin_loader` | 3 | 0.90 |
| `test_gui_plot_widget` | 16 | 0.83 |
| `test_review_importers_video` | 2 | 0.79 |
| `test_sample_interval` | 3 | 0.75 |
| `test_ai_panel` | 7 | 0.49 |
| `test_setup_environment` | 8 | 0.27 |
| `test_folder_info` | 11 | 0.27 |
| `test_navigator_controls` | 9 | 0.16 |
| `test_plugin_manager` | 2 | 0.10 |
| `test_gui_video_plot_widget` | 1 | 0.07 |
| `test_import_batch` | 7 | 0.03 |
| `test_gui_internal_log` | 2 | 0.02 |
| `test_gnss_distance` | 1 | 0.01 |
| `test_plot_policy` | 8 | 0.01 |
| `test_gnss_import_recovery` | 4 | 0.00 |
| `test_folder_date_parser` | 3 | 0.00 |
| `test_parser_base` | 2 | 0.00 |
| `test_graphics_gui` | 1 | 0.00 |
| `setUpClass (test_video_gui.DesktopTests)` | 1 | 0.00 |

## All tests

Grouped by module and class, in run order.

### `test_ai_panel.AIPanelTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_a_question_and_its_files_go_to_the_conversation` | 0.22 | passed |
| `test_dropping_files_on_a_minimized_panel_expands_it_and_attaches_them` | 0.06 | passed |
| `test_files_attach_once_and_chips_remove_them` | 0.06 | passed |
| `test_input_grows_to_ten_lines_over_the_conversation` | 0.07 | passed |
| `test_minimized_panel_is_one_row_that_expands_when_clicked` | 0.02 | passed |
| `test_provider_selector_and_sign_in` | 0.02 | passed |
| `test_send_button_becomes_stop_while_the_agent_is_busy` | 0.03 | passed |

### `test_app_settings.AppSettingsTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_active_cache_defers_the_file_write_until_shutdown_flush` | 0.02 | passed |
| `test_defaults_include_hdf5_and_navigator_controls` | 0.01 | passed |
| `test_diagnostic_editor_picker_switches_and_priority_round_trip` | 0.92 | passed |
| `test_diagnostic_priority_ignores_unknown_and_duplicate_entries` | 0.00 | passed |
| `test_diagnostic_settings_migrate_legacy_switch_before_defaults_merge` | 0.06 | passed |
| `test_dialog_has_the_six_tabs_and_keeps_settings_changed_elsewhere` | 0.39 | passed |
| `test_editor_migrates_and_saves_independent_plot_heights` | 0.50 | passed |
| `test_light_theme_uses_dark_text_for_controls_and_placeholders` | 0.60 | passed |
| `test_local_debug_settings_take_precedence_over_committed_defaults` | 0.01 | passed |
| `test_navigator_control_settings_round_trip` | 0.03 | passed |
| `test_plot_heights_migrate_shared_setting_and_validate_per_card_values` | 0.00 | passed |
| `test_plot_settings_default_to_five_pixel_diagnostic_width` | 0.01 | passed |
| `test_recent_folders_do_not_persist_elided_display_text` | 0.02 | passed |
| `test_saving_sections_preserves_unrelated_sections` | 0.05 | passed |
| `test_settings_editor_uses_typed_controls_and_saves` | 0.24 | passed |
| `test_theme_preview_applies_immediately_and_is_saved` | 0.99 | passed |

### `test_audio_media.AudioAttachmentTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_audio_file_is_linked_and_queued_for_conversion` | 0.74 | passed |
| `test_h5_audio_is_linked_without_conversion` | 0.60 | passed |
| `test_media_group_holds_videos_images_and_linked_audio` | 6.21 | passed |
| `test_scanned_audio_beside_parent_video_joins_linking_folder` | 3.13 | passed |
| `test_video_without_soundtrack_is_not_linked` | 0.61 | passed |

### `test_audio_media.AudioConversionTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_audio_jobs_queue_as_their_own_operation` | 0.00 | passed |
| `test_compression_none_writes_uncompressed_datasets` | 0.75 | passed |
| `test_failed_conversion_reports_and_leaves_no_file` | 0.05 | passed |
| `test_folder_log_start_ignores_audio_files` | 2.36 | passed |
| `test_offset_change_moves_log_start_only` | 2.74 | passed |
| `test_wav_converts_to_counts_with_lod_and_offset` | 2.41 | passed |

### `test_card_visibility.CardVisibilityTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_card_controls_hide_on_every_card_and_restore_their_states` | 1.04 | passed |
| `test_drag_strip_is_tinted_while_the_pointer_is_over_it` | 0.57 | passed |
| `test_legends_hide_on_every_card_and_return_as_each_card_wants` | 1.79 | passed |
| `test_navigator_buttons_emit_and_show_saved_states_quietly` | 0.09 | passed |
| `test_plot_navigator_button_hides_the_overview_and_keeps_the_toolbar` | 0.15 | passed |

### `test_core_startup.CoreStartupTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_new_plugin_defaults_preserve_sources_and_startup_preference` | 0.26 | passed |
| `test_start_open_cache_and_close_without_plugins` | 5.98 | passed |
| `test_start_options` | 1.51 | passed |

### `test_demo_data_app.DemoDataHelperTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_parse_progress` | 0.00 | passed |
| `test_progress_runs_through_the_stages_to_one` | 0.00 | passed |
| `test_remove_deletes_read_only_files` | 0.01 | passed |
| `test_uses_folder_finds_absolute_paths_inside_the_root` | 0.03 | passed |

### `test_demo_data_app.DemoDataJobTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_cancel_stops_the_generator` | 1.68 | passed |
| `test_failure_reports_the_generator_output` | 1.23 | passed |
| `test_job_reports_progress_and_completes` | 1.06 | passed |
| `test_replace_deletes_the_old_folder_first` | 1.27 | passed |

### `test_demo_data_app.DemoDataSettingsTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_general_tab_has_the_regenerate_button` | 0.26 | passed |

### `test_demo_data_app.DemoDataWindowTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_dont_ask_again_stops_the_startup_offer` | 2.04 | passed |
| `test_regenerate_closes_demo_cards_replaces_the_folder_and_reports` | 4.17 | passed |

### `test_demo_dataset.DemoCourseTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_imu_series_have_sample_loss_and_clipping` | 0.42 | passed |
| `test_long_course_is_light` | 0.69 | passed |
| `test_main_course_climbs_turns_and_alternates_feet` | 0.01 | passed |
| `test_timing_problems_are_present` | 0.38 | passed |

### `test_demo_dataset.DemoFileTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_csv_and_h5_carry_units_metadata_and_diagnostics` | 3.30 | passed |
| `test_workspace_lists_every_card_type` | 0.19 | passed |

### `test_demo_dataset.LensModelTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_lens_centres_look_forward_and_back` | 0.03 | passed |
| `test_theta_inverts_the_lens_model` | 0.00 | passed |

### `test_filmstrip.FilmstripTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_filmstrip_row_drags_its_video_and_moves_it` | 0.61 | passed |
| `test_frames_are_read_at_keyframes_with_the_video_size` | 0.21 | passed |
| `test_lane_shows_frames_at_their_data_time_beside_the_series` | 1.10 | passed |
| `test_navigator_drags_a_video_as_a_selection` | 0.20 | passed |
| `test_pending_frames_show_outlines_and_a_lane_can_be_selected_and_dragged` | 1.23 | passed |
| `test_selected_filmstrip_offset_is_its_video_sync_offset` | 1.03 | passed |
| `test_video_card_dropped_on_a_multi_series_legend_adds_a_filmstrip` | 0.72 | passed |
| `test_video_only_card_policy_and_drop_into_an_existing_card` | 0.58 | passed |
| `test_video_without_log_start_is_refused` | 0.44 | passed |

### `test_folder_date_parser.FolderDateParserTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_finds_closest_dated_parent` | 0.00 | passed |
| `test_invalid_folder_name_is_unknown` | 0.00 | passed |
| `test_parses_current_folder_name_format` | 0.00 | passed |

### `test_folder_info.FolderInfoTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_casing_variants_collapse_to_one_key` | 0.00 | passed |
| `test_empty_video_list_supersedes_legacy_link` | 0.00 | passed |
| `test_images_roundtrip_alongside_video_and_metadata` | 0.12 | passed |
| `test_invalid_image_collection_is_rejected` | 0.00 | passed |
| `test_invalid_video_collection_is_rejected` | 0.00 | passed |
| `test_legacy_video_migrates_without_losing_offset_or_metadata` | 0.06 | passed |
| `test_multiple_videos_roundtrip_with_independent_offsets` | 0.07 | passed |
| `test_non_object_section_is_rejected` | 0.00 | passed |
| `test_older_metadata_has_no_images` | 0.00 | passed |
| `test_split_info_json_canonicalises_key_casing` | 0.00 | passed |
| `test_write_info_file_uses_split_schema` | 0.02 | passed |

### `test_folder_info_transactions.FolderInfoTransactionTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_attachment_add_remove_and_sync_do_not_overwrite_each_other` | 0.24 | passed |
| `test_conflicting_edit_or_delete_fails_without_losing_saved_value` | 0.16 | passed |
| `test_export_snapshot_preserves_new_user_edit_and_attachment` | 0.12 | passed |
| `test_failed_replace_keeps_original_and_cleans_unique_temp` | 0.03 | passed |
| `test_reusing_a_saved_object_and_copying_to_another_folder` | 0.12 | passed |
| `test_simultaneous_processes_preserve_both_changes` | 2.20 | passed |

### `test_frequency_plot.FrequencyCardTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_card_renders_and_links_time_axes_markers_and_navigator` | 2.54 | passed |
| `test_fft_context_controls_recalculate_and_display_only_controls_reuse_data` | 1.52 | passed |
| `test_frequency_dialog_preview_and_band_zoom_survive_recalculation` | 2.19 | passed |
| `test_latest_request_wins_and_closing_does_not_wait_for_a_worker` | 0.91 | passed |
| `test_navigator_context_menu_opens_frequency_card` | 0.40 | passed |
| `test_peak_line_controls_and_marker_popup_use_same_frequency_without_recomputing` | 1.07 | passed |
| `test_peak_marker_hides_for_gaps_and_refreshes_after_new_data` | 0.70 | passed |
| `test_peak_threshold_filters_linear_amplitude_with_gaps_without_changing_marker_values` | 1.52 | passed |
| `test_relative_time_axis_preserves_millisecond_and_microsecond_ticks` | 0.07 | passed |
| `test_unreachable_target_is_visible_in_card_status` | 3.31 | passed |

### `test_frequency_plot.FrequencyDataTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_db_reference_and_missing_window_colours` | 0.00 | passed |
| `test_frequency_change_has_time_on_columns_and_frequency_on_rows` | 0.08 | passed |
| `test_high_resolution_band_separates_close_tones_and_preserves_fft_amplitudes` | 0.26 | passed |
| `test_irregular_timing_is_rejected` | 0.08 | passed |
| `test_missing_values_and_timestamp_gaps_remain_blank` | 0.11 | passed |
| `test_overview_is_bounded_and_zoom_uses_dense_windows` | 0.11 | passed |
| `test_peak_track_excludes_band_edges_and_breaks_at_missing_or_silent_windows` | 0.09 | passed |
| `test_requested_resolution_uses_longer_real_windows_and_validates_inputs` | 0.00 | passed |
| `test_selected_band_uses_image_budget_and_short_ranges_report_actual_window` | 0.11 | passed |
| `test_time_units_and_canonical_alignment_do_not_change_frequencies` | 0.27 | passed |
| `test_window_gain_dc_and_nyquist_amplitudes` | 0.01 | passed |

### `test_gnss_distance.DistanceTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_shared_distance_uses_metres_and_segment_boundaries` | 0.01 | passed |

### `test_gnss_import_recovery.GnssTimeFilterTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_duplicate_outlier_channels_do_not_outvote_main_recording` | 0.00 | passed |
| `test_empty_single_and_equal_size_clusters` | 0.00 | passed |
| `test_exactly_24_hours_and_continuous_multiple_days_are_kept` | 0.00 | passed |
| `test_isolated_past_and_future_groups_are_removed` | 0.00 | passed |

### `test_graphics_gui.GraphicsCompositionTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_map_and_video_in_both_creation_orders` | 0.00 | skipped |

### `test_gui_data_drop.DataDropTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_card_outline_follows_individual_cards_in_scrolled_horizontal_row` | 1.56 | passed |
| `test_ctrl_changes_axis_preview_and_native_cursor_action` | 0.94 | passed |
| `test_delayed_selector_creates_card_between_rows` | 0.94 | passed |
| `test_drop_hints_switch_between_card_row_legend_gap_and_workspace_gap` | 0.78 | passed |
| `test_early_drop_retains_default_and_cancel_clears_timer` | 0.74 | passed |
| `test_event_and_map_insertion_keep_parallel_state_order` | 0.47 | passed |
| `test_image_selector_inserts_before_horizontal_row` | 0.59 | passed |
| `test_intercepted_canvas_drag_does_not_forward_unmatched_leave` | 0.61 | passed |
| `test_internal_drag_survives_deleted_source_label_without_removal` | 0.73 | passed |
| `test_internal_reorder_both_directions_preserves_colors_and_card` | 3.41 | passed |
| `test_legend_drop_from_nested_widgets_in_scrolled_workspace` | 3.19 | passed |
| `test_legend_drop_inserts_and_canvas_drop_appends_with_copy_action` | 1.55 | passed |
| `test_metadata_options_and_settings_round_trip` | 0.91 | passed |
| `test_metadata_results_follow_latest_drag_and_do_not_reopen_after_cancel` | 0.33 | passed |
| `test_move_from_other_legend_replaces_xy_axis_and_removes_source` | 1.75 | passed |
| `test_native_drag_lifetime_cleans_up_on_cancel` | 0.19 | passed |
| `test_navigator_attachment_drops_beside_a_media_card` | 1.68 | passed |
| `test_navigator_selection_opening_a_map_drops_beside_a_media_card` | 3.01 | passed |
| `test_numeric_on_image_and_attachments_on_numeric_are_rejected` | 0.84 | passed |
| `test_selector_survives_child_transition_and_resets_for_new_gap` | 0.43 | passed |
| `test_side_drop_needs_side_by_side_cards_on_both_ends` | 1.16 | passed |
| `test_xy_group_label_replacement_and_axis_swap` | 1.11 | passed |

### `test_gui_gnss_3d_plot_widget.Gnss3DControlsTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_controls_changed_before_map_load_are_applied_when_ready` | 0.41 | passed |
| `test_defaults_and_near_ground_pitch_limit` | 0.23 | passed |
| `test_explicit_height_overrides_recorded_altitude` | 0.85 | passed |
| `test_hidden_or_fully_transparent_map_does_not_occlude_track` | 0.57 | passed |
| `test_live_exaggeration_and_offsets_do_not_accumulate_or_change_camera` | 0.28 | passed |
| `test_markers_selection_values_boxes_and_clicks` | 0.30 | passed |
| `test_occlusion_choice_survives_loading_and_refresh` | 0.43 | passed |
| `test_occlusion_switch_replaces_overlay_without_resetting_track_or_camera` | 0.26 | passed |
| `test_plain_wheel_scrolls_workspace_and_ctrl_wheel_reaches_browser` | 0.18 | passed |
| `test_terrain_toggle_and_track_refresh_retain_slider_values` | 0.24 | passed |
| `test_theme_background_survives_map_visibility_changes` | 0.28 | passed |
| `test_track_without_altitude_draws_flat_at_zero` | 0.62 | passed |

### `test_gui_gnss_plot_widget.GnssCoordinateTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_event_plot_caps_text_items_to_available_pixel_width` | 0.27 | passed |
| `test_event_series_uses_file_time_origin_not_its_first_sample` | 0.06 | passed |
| `test_event_series_window_keeps_transitions_on_both_sides` | 0.07 | passed |
| `test_event_state_plot_keeps_two_event_series_in_legend_and_y_axis` | 0.32 | passed |
| `test_event_state_plot_renders_state_intervals_from_h5_labels` | 0.15 | passed |
| `test_normalises_legacy_fixed_point_coordinates_for_map` | 0.00 | passed |
| `test_playback_markers_wait_for_ready_and_coalesce` | 0.03 | passed |

### `test_gui_grouped_legends.GroupedLegendTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_3d_gnss_group_updates_keep_camera_and_render_both_colors` | 0.89 | passed |
| `test_all_grouped_cards_have_header_color_and_wait_for_empty_axes` | 6.06 | passed |
| `test_axis_move_to_existing_ordinary_card_leaves_source_empty` | 2.72 | passed |
| `test_axis_move_to_new_card_leaves_source_empty` | 1.42 | passed |
| `test_axis_move_to_new_group_tracks_original_group_after_insertion` | 1.44 | passed |
| `test_axis_moves_between_grouped_cards_leave_source_empty` | 22.64 | passed |
| `test_axis_swap_with_empty_slot_clears_only_original_assignment` | 1.93 | passed |
| `test_cancelled_axis_drag_leaves_source_intact` | 0.56 | passed |
| `test_cancelled_group_drag_leaves_source_intact` | 0.57 | passed |
| `test_ctrl_on_another_card_or_new_group_still_moves` | 3.40 | passed |
| `test_ctrl_swap_between_groups_preserves_both_assignments` | 1.48 | passed |
| `test_gnss_coordinates_can_come_from_different_files_and_time_units` | 0.27 | passed |
| `test_group_can_move_to_an_ordinary_line_card` | 1.12 | passed |
| `test_group_reordering_and_color_change_use_shared_controls` | 1.58 | passed |
| `test_grouped_card_outline_axis_outline_and_group_insertion_line_are_exclusive` | 2.00 | passed |
| `test_same_card_axis_move_or_ctrl_swap_across_all_grouped_cards` | 5.35 | passed |
| `test_scrolled_axis_drop_and_drag_edge_scrolling_stop_on_cancel` | 1.99 | passed |
| `test_selector_creates_partial_grouped_cards_and_legend_completes_them` | 5.18 | passed |
| `test_three_axes_split_into_xy_pairs_and_move_keeps_group_color` | 2.14 | passed |
| `test_time_axis_move_preserves_component` | 1.23 | passed |
| `test_two_axes_transferred_to_3d_leave_z_empty` | 1.55 | passed |

### `test_gui_internal_log.InternalLogPanelTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_plain_text_panel_evicts_oldest_lines_at_configured_limit` | 0.01 | passed |

### `test_gui_internal_log.NativeStderrLogCaptureTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_stop_ends_the_reader_and_restores_valid_standard_handles` | 0.00 | passed |

### `test_gui_lodgazilla.CombinedProcessProgressTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_parallel_progress_uses_the_longest_remaining_process` | 0.00 | passed |

### `test_gui_lodgazilla.ImportPluginSelectionTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_folder_import_uses_current_gui_plugins_when_disk_settings_disagree` | 0.17 | passed |

### `test_gui_lodgazilla.ImportSchedulingTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_coordinator_starts_independent_queued_work_behind_a_conflict` | 0.00 | passed |
| `test_finishing_a_job_releases_slots_for_the_next_queued_job` | 0.00 | passed |
| `test_h5_and_csv_work_can_share_an_overlapping_folder` | 0.00 | passed |
| `test_recursive_scope_conflict_and_mixed_operations_follow_policy` | 0.00 | passed |
| `test_two_h5_requests_for_an_overlapping_folder_still_conflict` | 0.00 | passed |

### `test_gui_lodgazilla.PlotWorkspaceTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_card_types_use_independent_heights_without_resizing_existing_cards` | 2.09 | passed |
| `test_line_plot_overlays_multiple_series_and_allows_color_changes` | 0.14 | passed |
| `test_line_plot_renders_h5_series_instead_of_placeholder_text` | 0.11 | passed |
| `test_marker_settings_defaults_and_style_apply_to_open_cards` | 0.44 | passed |
| `test_new_plot_keeps_configured_height` | 0.17 | passed |
| `test_plot_cards_can_collect_series_reorder_and_close` | 0.28 | passed |
| `test_plot_navigator_places_and_removes_both_markers` | 0.12 | passed |
| `test_second_marker_and_selection_follow_the_marker_link` | 0.63 | passed |
| `test_time_axis_on_all_cards_the_bottom_card_or_none` | 0.38 | passed |
| `test_time_axis_representation_is_linked_by_default` | 0.29 | passed |

### `test_gui_multi_plot_widget.MultiPlotTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_axis_wheel_without_ctrl_scrolls_without_changing_series_scales` | 0.58 | passed |
| `test_axis_wheel_without_selection_zooms_all_series_vertically` | 0.94 | passed |
| `test_batch_add_preserves_deselection_and_removing_selected_series_does_not_select_another` | 0.81 | passed |
| `test_event_state_pixel_height_matches_across_cards_and_survives_zoom_and_resize` | 1.41 | passed |
| `test_folder_open_and_workspace_drop_linking` | 0.93 | passed |
| `test_independent_scales_survive_reload_add_remove_and_settings` | 0.67 | passed |
| `test_left_click_empty_space_deselects_trace_and_right_click_keeps_selection` | 0.84 | passed |
| `test_left_drag_on_empty_space_pans_without_deselecting_trace` | 0.21 | passed |
| `test_marker_snapping_ignores_synthetic_state_endpoint` | 0.66 | passed |
| `test_mouse_selection_drag_and_axis_zoom_only_change_selected` | 0.63 | passed |
| `test_new_series_remain_unselected_and_existing_choice_survives_insertions` | 0.91 | passed |
| `test_numeric_axis_uses_line_axis_tick_spacing_and_precision` | 0.33 | passed |
| `test_right_click_and_trace_click_place_markers_around_a_time_range` | 0.35 | passed |
| `test_series_menu_multi_and_group_menu_default` | 0.99 | passed |
| `test_state_event_bars_labels_and_independent_transforms` | 0.59 | passed |
| `test_tree_and_legend_drops_and_self_drop_rejection` | 0.74 | passed |
| `test_untyped_events_remain_discrete_and_labels_identify_states` | 0.42 | passed |

### `test_gui_navigator_widget.NavigatorColumnProfileTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_column_settings_are_saved_per_category` | 0.09 | passed |
| `test_columns_are_scoped_by_category` | 0.09 | passed |
| `test_row_types_are_mapped_to_their_column_categories` | 0.00 | passed |

### `test_gui_navigator_widget.NavigatorDiagnosticRowStyleTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_column_settings_save_falls_back_when_atomic_replace_is_denied` | 0.02 | passed |
| `test_diagnostics_style_series_and_propagate_to_parents` | 2.78 | passed |
| `test_sample_loss_and_clipping_buttons_switch_and_save_the_diagnostics` | 5.54 | passed |

### `test_gui_navigator_widget.NavigatorFolderSortingTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_header_cycles_sort_and_preserves_selection_and_subtrees` | 3.78 | passed |
| `test_numeric_metadata_duration_refresh_and_inline_edit` | 3.99 | passed |
| `test_sort_is_limited_to_folder_profile_and_root_folder_rows` | 3.50 | passed |

### `test_gui_navigator_widget.NavigatorInfoGroupingTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_empty_detailed_info_tree_item_sorts_after_real_details` | 0.00 | passed |
| `test_folder_info_group_path_uses_value_empty_and_missing_groups` | 0.01 | passed |
| `test_info_tree_item_sorts_before_other_children` | 0.00 | passed |
| `test_metadata_filter_supports_choices_free_text_and_empty_values` | 0.11 | passed |
| `test_scan_folder_info_files_finds_editable_and_readonly_keys` | 0.04 | passed |

### `test_gui_navigator_widget.NavigatorSeriesDetailsTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_folder_log_length_uses_root_time_range_metadata` | 0.05 | passed |
| `test_format_sample_rate_keeps_compact_units` | 0.00 | passed |
| `test_read_h5_series_calculates_sample_rate_from_first_and_last_time` | 0.08 | passed |
| `test_read_h5_series_uses_root_series_index_metadata` | 0.02 | passed |

### `test_gui_navigator_widget.NavigatorSettingsIntegrationTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_align_columns_setting_applies_active_widths_by_position` | 6.20 | passed |
| `test_clicking_logical_value_cell_preserves_horizontal_scroll_position` | 7.31 | passed |
| `test_clicking_sample_count_at_scroll_origin_does_not_hide_data_column` | 4.95 | passed |
| `test_copy_uses_selected_logical_value_cell` | 2.90 | passed |
| `test_double_click_series_group_only_toggles_expansion` | 3.42 | passed |
| `test_double_clicking_editable_logical_value_opens_inline_editor_without_scrolling` | 3.24 | passed |
| `test_folder_metadata_column_protects_readonly_values` | 3.93 | passed |
| `test_horizontal_scrollbar_uses_the_widest_row_type_configuration` | 3.40 | passed |
| `test_hover_tracks_only_the_logical_value_cell` | 2.54 | passed |
| `test_leaving_the_folder_field_unchanged_keeps_the_tree` | 4.36 | passed |
| `test_logical_column_width_and_order_are_customisable` | 2.87 | passed |
| `test_logical_detailed_info_value_edit_updates_info_file` | 3.02 | passed |
| `test_logical_folder_value_edit_updates_info_file` | 2.20 | passed |
| `test_missing_folder_metadata_can_be_added_with_inline_editor` | 3.28 | passed |
| `test_navigator_control_settings_can_be_reloaded_without_restart` | 3.08 | passed |
| `test_navigator_controls_use_and_save_shared_settings` | 5.69 | passed |
| `test_refresh_tree_updates_series_columns_from_recreated_h5_metadata` | 3.77 | passed |
| `test_selecting_a_different_row_type_preserves_horizontal_scroll_position` | 3.64 | passed |
| `test_series_group_right_click_exposes_child_series` | 2.76 | passed |
| `test_single_click_selects_and_copies_logical_value_cell` | 3.83 | passed |
| `test_tree_uses_data_and_value_physical_columns` | 3.31 | passed |
| `test_ui_only_refresh_reuses_h5_and_folder_info_snapshot` | 3.45 | passed |
| `test_uses_most_recent_folder_when_no_initial_folder_is_given` | 4.49 | passed |

### `test_gui_grouped_legends.GroupedLegendTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_3d_gnss_group_updates_keep_camera_and_render_both_colors` | 1.34 | passed |
| `test_all_grouped_cards_have_header_color_and_wait_for_empty_axes` | 3.99 | passed |
| `test_axis_move_to_existing_ordinary_card_leaves_source_empty` | 1.46 | passed |
| `test_axis_move_to_new_card_leaves_source_empty` | 1.17 | passed |
| `test_axis_move_to_new_group_tracks_original_group_after_insertion` | 1.04 | passed |
| `test_axis_moves_between_grouped_cards_leave_source_empty` | 8.82 | passed |
| `test_axis_swap_with_empty_slot_clears_only_original_assignment` | 0.87 | passed |
| `test_cancelled_axis_drag_leaves_source_intact` | 0.41 | passed |
| `test_cancelled_group_drag_leaves_source_intact` | 0.75 | passed |
| `test_ctrl_on_another_card_or_new_group_still_moves` | 1.94 | passed |
| `test_ctrl_swap_between_groups_preserves_both_assignments` | 1.31 | passed |
| `test_gnss_coordinates_can_come_from_different_files_and_time_units` | 0.24 | passed |
| `test_group_can_move_to_an_ordinary_line_card` | 1.07 | passed |
| `test_group_reordering_and_color_change_use_shared_controls` | 1.43 | passed |
| `test_grouped_card_outline_axis_outline_and_group_insertion_line_are_exclusive` | 2.16 | passed |
| `test_same_card_axis_move_or_ctrl_swap_across_all_grouped_cards` | 4.76 | passed |
| `test_scrolled_axis_drop_and_drag_edge_scrolling_stop_on_cancel` | 2.05 | passed |
| `test_selector_creates_partial_grouped_cards_and_legend_completes_them` | 5.95 | passed |
| `test_three_axes_split_into_xy_pairs_and_move_keeps_group_color` | 1.61 | passed |
| `test_time_axis_move_preserves_component` | 2.63 | passed |
| `test_two_axes_transferred_to_3d_leave_z_empty` | 1.73 | passed |

### `test_gui_plot_policy.PlotPolicyGuiTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_active_menu_is_first_and_enabled_only_for_compatible_card` | 1.01 | passed |
| `test_card_clicks_preserve_activation_and_closing_other_card_preserves_selection` | 1.21 | passed |
| `test_close_all_cancels_pending_activation` | 0.20 | passed |
| `test_double_click_fills_active_group_and_falls_back_for_incompatible_card` | 1.28 | passed |
| `test_double_click_uses_active_card_only_when_it_is_the_default_card` | 2.50 | passed |
| `test_early_multi_drop_before_metadata_is_ready` | 0.46 | passed |
| `test_empty_workspace_click_clears_active_card_and_next_activation_opens_new` | 1.09 | passed |
| `test_escape_clears_active_card_from_workspace_and_card_focus` | 0.45 | passed |
| `test_escape_outside_workspace_or_in_popup_keeps_active_card` | 0.44 | passed |
| `test_explicit_xy_keeps_three_series_in_one_card_with_empty_axis` | 0.61 | passed |
| `test_frequency_keeps_all_selection_with_media_and_state_fallbacks` | 1.45 | passed |
| `test_gnss_menu_requires_complete_numeric_coordinates` | 0.52 | passed |
| `test_gnss_sibling_detection_respects_separate_group_metadata` | 0.30 | passed |
| `test_group_gnss_actions_open_only_coordinates_and_plot_keeps_all_series` | 1.58 | passed |
| `test_individual_series_menu_finds_coordinate_siblings` | 0.71 | passed |
| `test_menu_active_then_default_and_partial_choice_opens_everything` | 1.27 | passed |
| `test_multi_selection_can_drop_into_existing_card` | 0.84 | passed |
| `test_multi_selection_drag_leave_and_default_drop_open_both_series` | 1.67 | passed |
| `test_navigator_drag_contains_every_selected_source` | 0.28 | passed |
| `test_series_group_activation_does_not_plot_and_menu_has_only_plot` | 0.98 | passed |
| `test_shared_default_opening_and_coordinate_order` | 2.09 | passed |
| `test_single_activation_uses_multi_for_numeric_and_state` | 0.64 | passed |

### `test_gui_plot_rows.PlotRowTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_attachment_removal_finds_nested_cards` | 0.34 | passed |
| `test_card_drag_is_intercepted_over_child_content_and_series_drag_is_not` | 0.27 | passed |
| `test_drag_auto_scroll_updates_preview` | 0.23 | passed |
| `test_drop_preview_selects_sides_and_vertical_rows_and_clears_on_cancel` | 0.41 | passed |
| `test_group_resize_reorder_and_restore_standalone_height` | 0.37 | passed |
| `test_linked_markers_and_theme_updates_reach_nested_maps` | 0.44 | passed |
| `test_move_between_groups_and_close_collapses_rows` | 0.82 | passed |
| `test_navigator_order_and_activation_include_nested_cards` | 0.82 | passed |
| `test_own_row_edge_can_ungroup_and_empty_workspace_space_accepts_drop` | 0.58 | passed |
| `test_row_height_respects_both_cards_minimum` | 0.36 | passed |
| `test_time_plots_and_narrow_targets_reject_horizontal_drop` | 0.34 | passed |

### `test_gui_plot_widget.MarkerDataLoadCoordinatorTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_coalesces_pending_requests_and_detaches_running_loader_on_shutdown` | 0.00 | passed |

### `test_gui_plot_widget.PlotSeriesLoadingTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_converts_device_relative_time_to_a_common_real_time_axis` | 0.05 | passed |
| `test_diagnostic_regions_expand_to_the_minimum_display_span` | 0.01 | passed |
| `test_loads_and_displays_sample_loss_regions` | 0.14 | passed |
| `test_loads_legacy_sample_loss_list_for_existing_caches` | 0.04 | passed |
| `test_loads_nearest_raw_sample_for_marker_popup` | 0.04 | passed |
| `test_loads_raw_series_with_title_and_unit` | 0.04 | passed |
| `test_loads_timing_delta_between_consecutive_samples` | 0.04 | passed |
| `test_lod_refresh_preserves_the_existing_view_range` | 0.10 | passed |
| `test_marker_sample_can_return_nearest_value_outside_series_window` | 0.04 | passed |
| `test_marker_sample_includes_converted_and_original_values` | 0.04 | passed |
| `test_opening_a_series_autoranges_once_then_disables_autorange` | 0.10 | passed |
| `test_resolves_navigator_series_name_when_hdf5_path_is_sanitized` | 0.04 | passed |
| `test_switches_from_lod_to_raw_samples_for_a_small_visible_range` | 0.06 | passed |
| `test_uses_hdf5_lod_envelope_when_raw_data_exceeds_budget` | 0.06 | passed |
| `test_xy_loader_aligns_y_to_x_times_and_honours_time_range` | 0.04 | passed |

### `test_gui_video_plot_widget.VideoPlotSupportTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_reads_the_first_valid_h5_log_start_and_skips_unreadable_files` | 0.07 | passed |

### `test_image_attachments.ImageAttachmentTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_activation_opens_real_card_and_rendering_preserves_aspect_ratio` | 2.76 | passed |
| `test_attach_multiple_relative_paths_and_skip_duplicates` | 4.07 | passed |
| `test_cancel_and_failed_save_leave_links_unchanged` | 3.12 | passed |
| `test_context_menu_identifies_clicked_image_and_offers_remove_link` | 3.05 | passed |
| `test_dragged_image_row_opens_card` | 3.88 | passed |
| `test_failed_removal_leaves_link_and_card` | 2.61 | passed |
| `test_missing_and_corrupt_images_show_errors` | 3.41 | passed |
| `test_relative_link_resolves_after_folder_relocation` | 3.02 | passed |
| `test_remove_link_only_closes_matching_folder_cards_and_preserves_file` | 4.54 | passed |

### `test_import_batch.ImportBatchTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_cache_write_files_never_reach_importer_selection` | 0.02 | passed |
| `test_distinct_default_hdf5_outputs_are_not_duplicates` | 0.01 | passed |
| `test_duplicate_default_hdf5_outputs_are_detected` | 0.00 | passed |
| `test_folder_info_json_is_not_an_import_source` | 0.00 | passed |
| `test_worker_count_does_not_exceed_job_count` | 0.00 | passed |
| `test_worker_count_is_capped` | 0.00 | passed |
| `test_worker_count_respects_per_import_limit` | 0.00 | passed |

### `test_import_helpers.CacheOwnershipTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_cache_from_a_moved_folder_is_replaced` | 0.12 | passed |
| `test_cache_of_another_source_is_preserved` | 0.05 | passed |
| `test_cache_of_the_same_source_is_replaced` | 0.13 | passed |
| `test_malformed_target_is_preserved` | 0.06 | passed |
| `test_replace_any_replaces_an_unrelated_h5` | 0.09 | passed |
| `test_unrelated_h5_is_preserved` | 0.06 | passed |

### `test_import_helpers.Hdf5TimeDatasetTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_failed_hdf5_replace_preserves_existing_cache` | 0.17 | passed |
| `test_failed_hdf5_write_preserves_existing_cache` | 0.09 | passed |
| `test_hdf5_writer_reports_monotonic_progress` | 0.14 | passed |
| `test_millisecond_series_uses_time_ms_and_matching_lod_datasets` | 0.35 | passed |

### `test_import_helpers.ImportSelectionTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_extension_is_only_hint_for_importer_selection` | 0.00 | passed |

### `test_import_helpers.LodTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_lod_buckets_preserve_min_and_max_values` | 0.01 | passed |

### `test_import_helpers.SampleLossTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_clipping_uses_inclusive_limits_and_groups_adjacent_samples` | 0.00 | passed |
| `test_detects_only_gaps_over_the_millisecond_limit` | 0.00 | passed |
| `test_hdf5_clipping_list_and_warning_metadata` | 0.24 | passed |
| `test_hdf5_sample_loss_list_stores_native_time_ranges` | 0.12 | passed |
| `test_parser_json_exposes_per_series_clipping_limits` | 0.00 | passed |

### `test_multi_series_lanes.LanePlacementTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_a_series_moved_to_the_top_leaves_lower_lanes_for_new_series` | 0.00 | passed |
| `test_stacks_one_lane_apart_then_fills_gaps_lowest_first` | 0.00 | passed |

### `test_multi_series_lanes.MultiSeriesLaneTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_collective_zoom_caps_at_tallest_series_and_zoom_out_responds_immediately` | 1.00 | passed |
| `test_collective_zoom_compresses_all_lanes_allows_overlap_and_restores_manual_positions` | 0.75 | passed |
| `test_constant_and_missing_values_do_not_break_collective_zoom` | 0.42 | passed |
| `test_horizontal_reload_keeps_full_range_and_collective_spacing_memory` | 0.75 | passed |
| `test_lane_height_setting_applies_to_new_series` | 0.39 | passed |
| `test_manual_move_after_compression_becomes_new_preferred_position` | 0.48 | passed |
| `test_partial_view_defers_raw_range_scan_to_background_reader` | 0.34 | passed |
| `test_raw_range_fallback_reads_all_chunks_and_ignores_nonfinite_values` | 0.12 | passed |
| `test_sample_loss_area_covers_only_the_first_series_lane` | 0.61 | passed |
| `test_series_fill_one_lane_each_without_overlap` | 0.54 | passed |
| `test_setting_is_bounded_and_saved_from_the_settings_dialog` | 0.93 | passed |

### `test_navigator_controls.NavigatorControlsTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_expand_icon_uses_contrast_in_light_and_dark_palettes` | 0.00 | passed |
| `test_fields_are_always_visible_and_share_their_right_edge` | 0.06 | passed |
| `test_filter_placeholders` | 0.03 | passed |
| `test_folder_edit_elides_display_but_keeps_full_text_and_tooltip` | 0.01 | passed |
| `test_folder_edit_never_replaces_the_full_path_with_its_display_elision` | 0.01 | passed |
| `test_folder_edit_updates_elision_after_resize` | 0.01 | passed |
| `test_middle_elide_text_reserves_more_space_for_path_end` | 0.00 | passed |
| `test_recent_folder_items_are_elided_without_widening_the_combo` | 0.01 | passed |
| `test_tree_buttons_are_centered_below_the_fields` | 0.04 | passed |

### `test_parser_base.ParserBaseTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_progress_reporter_throttles_and_forces_completion` | 0.00 | passed |
| `test_resolved_definition_uses_the_same_canonical_payload` | 0.00 | passed |

### `test_plot_policy.PlotPolicyTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_empty_and_duplicate_selection` | 0.00 | passed |
| `test_explicit_grouped_cards_keep_all_items_in_one_card` | 0.00 | passed |
| `test_frequency_uses_one_card_per_numeric_source_and_defaults_for_others` | 0.00 | passed |
| `test_gnss_actions_extract_only_complete_tracks_among_other_series` | 0.00 | passed |
| `test_gnss_roles_order_and_same_track_requirement` | 0.00 | passed |
| `test_media_remain_separate_and_do_not_change_numeric_default` | 0.00 | passed |
| `test_numeric_count_defaults` | 0.00 | passed |
| `test_states_use_multi_even_when_mixed_with_numeric` | 0.00 | passed |

### `test_plot_theme.PlotThemeTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_2d_and_3d_canvases_match_when_switching_themes` | 0.28 | passed |
| `test_map_pages_start_with_the_requested_canvas_color` | 0.00 | passed |
| `test_palette_broadcast_runs_without_garbage_collection` | 3.90 | passed |
| `test_video_clear_color_follows_canvas_theme` | 0.01 | passed |

### `test_plugin_contracts.CatalogTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_broken_importer_does_not_prevent_loading_others` | 0.23 | passed |
| `test_core_has_no_imports_from_installed_plugins` | 9.98 | passed |
| `test_dependency_stamp_includes_plugin_list_and_content` | 0.06 | passed |
| `test_empty_or_missing_plugin_directory_is_valid` | 0.01 | passed |
| `test_entry_point_cannot_escape_plugin_directory` | 0.04 | passed |
| `test_external_plugin_overrides_host_copy_in_combined_tests` | 0.10 | passed |
| `test_manifest_version_and_requirements_disable_only_the_affected_plugin` | 0.15 | passed |
| `test_nested_test_manifests_are_not_discovered` | 0.04 | passed |
| `test_snapshot_skips_local_data_and_logs_only_at_the_host_root` | 0.07 | passed |
| `test_worker_reload_uses_manifest_entry_point` | 0.06 | passed |

### `test_plugin_contracts.PluginJobTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_conflicts_include_plugin_identity_and_folder_scope` | 0.00 | passed |
| `test_real_spawned_worker_can_start_a_nested_pool` | 2.03 | passed |
| `test_registered_operation_uses_shared_budget_and_private_parameters` | 0.05 | passed |

### `test_plugin_contracts.PluginSettingsTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_local_settings_preserve_defaults_and_failed_writes` | 0.08 | passed |

### `test_plugin_loader.PluginLoaderTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_discovery_logs_a_broken_plugin_and_continues` | 0.08 | passed |
| `test_failed_load_does_not_leave_a_cached_module` | 0.79 | passed |
| `test_module_is_cached_by_resolved_path_and_namespace` | 0.03 | passed |

### `test_plugin_manager.PluginManagerTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_disabled_plugins_are_not_called_and_failures_are_isolated` | 0.04 | passed |
| `test_no_plugins_option_loads_none_even_when_enabled` | 0.06 | passed |

### `test_plugin_updates.DiscoveryTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_api_pagination_reads_all_pages` | 0.00 | passed |
| `test_api_refuses_pagination_to_other_hosts_before_sending_credentials` | 0.00 | passed |
| `test_bitbucket_uses_manifest_id_instead_of_repository_name` | 0.00 | passed |
| `test_github_private_and_public_results_are_filtered_and_deduplicated` | 0.00 | passed |
| `test_one_broken_repository_does_not_hide_other_plugins` | 0.04 | passed |
| `test_only_account_urls_are_accepted` | 0.00 | passed |
| `test_shared_manifest_validation_rejects_unsafe_paths_and_distinguishes_api` | 0.00 | passed |

### `test_plugin_updates.InstallationTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_dirty_checkout_is_not_updated` | 5.31 | passed |
| `test_edit_after_download_postpones_update` | 8.53 | passed |
| `test_incoming_file_cannot_overwrite_ignored_settings` | 9.26 | passed |
| `test_install_is_staged_and_activated_without_importing_code` | 5.34 | passed |
| `test_local_commits_are_never_discarded` | 15.85 | passed |
| `test_other_repository_cannot_replace_same_plugin_id` | 3.94 | passed |
| `test_update_preserves_settings_and_can_be_repeated` | 15.60 | passed |

### `test_plugin_updates.PluginSettingsTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_settings_roundtrip_and_results_keep_checkbox_selection` | 0.10 | passed |
| `test_spawned_worker_handles_invalid_source_and_shuts_down` | 0.94 | passed |

### `test_point_markers.PointMarkerTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_3d_plot_click_markers_selection_and_boxes` | 0.54 | passed |
| `test_map_selection_reports_points_and_distance` | 0.33 | passed |
| `test_video_timeline_shows_markers_and_selection_inside_the_video` | 0.33 | passed |
| `test_workspace_updates_video_timelines_when_markers_move` | 0.32 | passed |
| `test_xy_markers_link_with_time_cards` | 0.71 | passed |
| `test_xy_two_markers_selection_and_one_box_per_graph` | 0.69 | passed |

### `test_process_tree.ProcessTreeTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_failed_assignment_does_not_release_unowned_work` | 0.00 | passed |
| `test_forced_parent_exit_cleans_pool_and_converter_descendants` | 9.16 | passed |

### `test_review_app.BatchCancellationTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_cancel_terminates_pool_workers_that_do_not_cooperate` | 1.90 | passed |
| `test_import_job_stops_when_cancelled` | 0.47 | passed |
| `test_stale_cache_temp_files_are_swept` | 0.03 | passed |

### `test_review_app.CorruptSettingsTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_plain_load_does_not_move_the_file` | 0.12 | passed |
| `test_session_backs_up_a_corrupt_file_and_warns` | 0.31 | passed |

### `test_review_app.FolderMultiSeriesTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_folder_files_are_read_off_the_gui_thread` | 1.02 | passed |

### `test_review_app.Hdf5SettingsPropagationTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_import_process_receives_current_session_hdf5_settings` | 0.23 | passed |
| `test_worker_preserves_an_unrelated_h5_with_the_cache_name` | 0.26 | passed |
| `test_worker_writes_the_cache_with_the_supplied_settings` | 0.56 | passed |

### `test_review_app.InsertIntoCardTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_each_new_source_is_inserted_through_the_card_in_order` | 0.00 | passed |
| `test_line_card_keeps_existing_colors_when_inserting_above` | 0.76 | passed |

### `test_review_app.LogStartTimezoneTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_offset_is_resolved_for_the_logs_own_date` | 0.00 | passed |

### `test_review_app.MainWindowCloseTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_close_cancels_imports_before_terminating` | 0.00 | passed |
| `test_close_flushes_first_stops_plugins_and_disposes_every_card` | 26.27 | passed |
| `test_settings_are_saved_when_a_teardown_step_raises` | 22.77 | passed |

### `test_review_app.PluginLifecycleTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_disabled_plugins_are_not_instantiated_and_shutdown_stops_enabled` | 0.24 | passed |

### `test_review_app.SeriesTimeRangeCacheTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_range_is_read_once_until_the_file_changes` | 0.03 | passed |

### `test_review_app.SettingsFlushTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_failed_flush_is_retried_later` | 0.01 | passed |
| `test_session_change_notifies_the_registered_owner` | 0.14 | passed |

### `test_review_gnss_xy.FrequencyTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_g7_windows_after_an_early_gap_remain_valid` | 0.10 | passed |
| `test_g8_quantised_timestamps_keep_the_true_rate` | 0.19 | passed |

### `test_review_gnss_xy.Gnss3DAltitudeTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_2_8_card_draws_flat_and_follows_alt_reassignment` | 1.46 | passed |
| `test_2_missing_unreadable_and_nan_altitudes_draw_flat` | 0.30 | passed |
| `test_2_optional_axis_slot_is_loaded_while_required_slots_wait` | 0.03 | passed |
| `test_g5_3d_page_is_loaded_without_tracks` | 0.45 | passed |

### `test_review_gnss_xy.GnssTrackTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_g1_device_relative_map_times_use_the_file_origin` | 0.25 | passed |
| `test_g2_one_coordinate_scale_per_series` | 0.37 | passed |
| `test_g5_2d_map_receives_tracks_only_after_ready` | 0.02 | passed |
| `test_g5_g6_map_pages_carry_no_track_data_and_pushed_json_is_inert` | 0.01 | passed |
| `test_r4_explicit_units_cover_zero_crossings_and_radians_in_both_maps` | 0.51 | passed |

### `test_review_gnss_xy.ImageTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_g9_decoding_is_scaled_and_off_the_gui_thread` | 0.11 | passed |
| `test_g9_late_decode_after_dispose_is_ignored` | 0.04 | passed |

### `test_review_gnss_xy.XYTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_7_contains_time_uses_sorted_bounds` | 0.01 | passed |
| `test_g3_mixed_time_domains_are_rejected` | 0.07 | passed |
| `test_g3_x_samples_outside_y_coverage_are_dropped` | 0.22 | passed |
| `test_g4_click_ignores_nan_samples` | 0.36 | passed |
| `test_r5_coverage_has_no_half_sample_extrapolation` | 0.25 | passed |

### `test_review_importers_video.DamagedInsvTrailerTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_load_metadata_falls_back_and_logs_warning` | 0.10 | passed |
| `test_session_plays_insv_with_damaged_trailer` | 0.69 | passed |

### `test_review_navigator.AddMetadataRowEditorTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_add_metadata_row_editor_is_empty_and_untouched_commit_is_ignored` | 7.50 | passed |
| `test_typed_value_in_add_metadata_row_is_saved` | 8.96 | passed |
| `test_unchanged_value_commit_does_not_write` | 8.43 | passed |

### `test_review_navigator.BackgroundSnapshotTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_background_refresh_reads_disk_off_the_gui_thread` | 9.00 | passed |
| `test_deferred_initial_load_scans_in_the_background` | 7.63 | passed |
| `test_edit_during_background_job_survives_the_job_result` | 13.97 | passed |
| `test_first_background_load_shows_loading_then_tree` | 14.24 | passed |
| `test_folder_switch_before_deferred_initial_load_wins` | 10.85 | passed |
| `test_metadata_edit_updates_index_without_rescanning` | 9.16 | passed |
| `test_ui_only_background_refresh_does_not_rebuild_when_disk_is_unchanged` | 5.50 | passed |

### `test_review_navigator.DefaultReadonlyKeyTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_default_key_does_not_override_readonly_value` | 0.03 | passed |
| `test_navigator_does_not_open_editor_for_readonly_default_key` | 5.47 | passed |

### `test_review_navigator.DeletedFolderRefreshTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_existing_info_reader_never_creates_files` | 0.01 | passed |
| `test_refresh_after_deleting_folder_does_not_recreate_it` | 5.25 | passed |

### `test_review_navigator.DetailedInfoReentryTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_key_rename_writes_once` | 4.81 | passed |

### `test_review_navigator.FilterExactMatchTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_listed_value_is_exact_and_free_text_is_substring` | 0.04 | passed |

### `test_review_navigator.NavigatorDataLayerTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_snapshot_job_imports_no_gui_modules` | 9.65 | passed |

### `test_review_navigator.OutsideRootLinkTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_link_outside_root_is_dropped` | 5.04 | passed |

### `test_review_navigator.ProgressExpansionTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_expanded_folder_with_progress_stays_expanded` | 4.91 | passed |

### `test_review_navigator.SelectionPreservationTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_refresh_keeps_current_series_and_logical_column` | 4.94 | passed |

### `test_review_navigator.SettingsDialogThemeTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_cancel_after_apply_restores_saved_theme` | 0.21 | passed |

### `test_review_plots.DropInspectionTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_failed_inspection_completes_and_allows_the_next_drag` | 0.06 | passed |

### `test_review_plots.LinkedViewRangeTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_workspace_follows_and_sets_the_linked_view_range` | 0.59 | passed |

### `test_review_plots.NavigatorReorderTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_drop_index_matches_the_drawn_insertion_line` | 0.05 | passed |

### `test_review_plots.NavigatorViewBoxTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_active_card_bar_takes_precedence_over_the_view_box_edge` | 0.07 | passed |
| `test_dragging_inside_the_view_box_pans_and_a_click_still_places_a_marker` | 0.07 | passed |
| `test_dragging_view_box_edges_zooms_without_touching_markers_or_cards` | 0.06 | passed |

### `test_review_plots.SelectionTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_marker_drag_reports_one_selection_on_release_and_right_drag_places_nothing` | 0.09 | passed |
| `test_statistics_are_limited_to_the_data_time_range` | 0.15 | passed |
| `test_statistics_coordinator_runs_one_loader_and_keeps_only_the_latest_request` | 0.01 | passed |

### `test_review_plots.SeriesColorTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_color_helpers_prefer_unused_palette_colors` | 0.01 | passed |
| `test_event_state_insert_and_removal_keep_original_series_colors` | 0.66 | passed |
| `test_line_card_insert_and_removal_keep_original_series_colors` | 0.54 | passed |
| `test_new_legend_group_uses_an_unused_color` | 0.01 | passed |

### `test_review_plots.TimeAndMarkerTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_elapsed_axis_counts_from_log_start_for_unix_time` | 0.05 | passed |
| `test_marker_canvases_repaint_fully_so_old_markers_are_erased` | 0.25 | passed |
| `test_marker_colors_follow_the_requested_series_index` | 0.09 | passed |
| `test_raw_view_includes_one_sample_beyond_each_edge` | 0.08 | passed |
| `test_unix_marker_log_start_uses_the_common_log_start` | 0.16 | passed |

### `test_review_plots.VisibleWindowTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_background_load_failure_is_shown_on_the_line_card` | 0.33 | passed |
| `test_data_range_change_loads_in_background_and_keeps_the_view` | 0.21 | passed |
| `test_event_state_data_range_change_loads_in_background_and_keeps_the_view` | 0.71 | passed |
| `test_event_state_series_assignment_loads_in_background` | 0.89 | passed |
| `test_event_state_view_set_while_loading_is_kept` | 0.61 | passed |
| `test_event_state_window_reload_runs_in_background` | 0.35 | passed |
| `test_line_pan_loads_in_background_and_survives_a_missing_cache` | 0.23 | passed |
| `test_series_assignment_loads_in_background_and_auto_ranges` | 0.31 | passed |
| `test_stale_series_load_does_not_replace_a_newer_assignment` | 0.18 | passed |
| `test_view_set_while_loading_is_kept` | 0.31 | passed |

### `test_sample_interval.SampleIntervalTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_intervals_are_converted_to_seconds` | 0.07 | passed |
| `test_offered_for_numeric_and_state_series_and_packs_into_one_card` | 0.07 | passed |
| `test_workspace_opens_card_and_accepts_more_series` | 0.61 | passed |

### `test_sample_limit.SampleLimitDataTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_grouped_loader_passes_a_card_limit_only_when_set` | 0.03 | passed |
| `test_loaders_honour_the_limit_and_all_keeps_every_sample` | 0.15 | passed |
| `test_loaders_report_drawn_and_available_samples` | 0.09 | passed |
| `test_percent_text_never_rounds_a_partial_view_to_100` | 0.02 | passed |
| `test_step_and_labels` | 0.02 | passed |

### `test_sample_limit.SampleLimitSelectorTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_changing_the_limit_keeps_the_3d_axis_scaling` | 0.14 | passed |
| `test_changing_the_limit_keeps_the_xy_zoom_and_position` | 0.14 | passed |
| `test_header_row_places_left_and_right_controls_beside_the_drag_strip` | 0.06 | passed |
| `test_help_button_lists_the_cards_mouse_actions` | 0.24 | passed |
| `test_map_cards_default_to_twenty_thousand_points` | 0.00 | passed |
| `test_selector_defaults_sits_left_of_close_and_reloads` | 0.29 | passed |
| `test_selector_shows_drawn_share_and_bold_when_partial` | 0.14 | passed |

### `test_selection_duration.SelectionDurationTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_duration_units_and_summary_survive_marker_updates` | 0.10 | passed |
| `test_duration_uses_native_units_or_canonical_seconds_independently_of_axis_display` | 0.12 | passed |
| `test_empty_selection_and_timing_delta_keep_duration_without_waiting_for_statistics` | 0.42 | passed |
| `test_frequency_settings_menu_is_on_the_header_button` | 0.58 | passed |
| `test_two_markers_select_the_area_between_on_all_time_card_types` | 1.88 | passed |

### `test_setup_environment.SetupEnvironmentTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_activation_precedes_dependency_discovery` | 0.05 | passed |
| `test_check_does_not_activate_pending_plugins` | 0.01 | passed |
| `test_check_without_stamp_does_not_install_or_write_files` | 0.02 | passed |
| `test_custom_plugin_root_matches_runtime_environment` | 0.03 | passed |
| `test_failed_install_does_not_save_success_stamp` | 0.02 | passed |
| `test_install_and_validation_use_selected_python_without_creating_venv` | 0.02 | passed |
| `test_run_reports_a_failed_install_instead_of_raising` | 0.02 | passed |
| `test_stamp_skips_reinstall_but_changed_interpreter_invalidates_it` | 0.10 | passed |

### `test_time_offset.NavigatorOffsetDragTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_sideways_drag_sets_offset_and_vertical_drag_reorders` | 0.09 | passed |
| `test_trimmed_entries_offer_reset_selection` | 0.01 | passed |

### `test_time_offset.TimeOffsetTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_axis_labels_show_shared_time` | 0.01 | passed |
| `test_image_cards_have_no_time_offset` | 0.02 | passed |
| `test_multi_series_offsets_only_the_selected_series` | 1.30 | passed |
| `test_offset_button_opens_an_editor_in_its_place_until_focus_leaves` | 0.49 | passed |
| `test_offset_moves_card_view_markers_and_navigator_bar` | 0.88 | passed |
| `test_restored_card_keeps_its_offset` | 1.25 | passed |

### `test_value_boxes.SeriesPaletteTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_default_colors_stay_clear_of_the_selection_and_marker_hues` | 0.00 | passed |

### `test_value_boxes.ValueBoxTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_background_opacity_follows_the_setting` | 0.67 | passed |
| `test_details_follow_marker_moves_and_show_selection_statistics` | 0.86 | passed |
| `test_hover_shows_details_and_a_click_pins_them_until_a_click_outside` | 0.45 | passed |
| `test_one_box_per_series_at_each_marker_beside_its_line` | 0.54 | passed |
| `test_sample_interval_boxes_show_only_selection_statistics` | 0.81 | passed |

### `test_video_attachments.VideoAttachmentTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_attach_appends_files_and_preserves_existing_offset` | 6.52 | passed |
| `test_cancel_does_not_change_links` | 5.77 | passed |
| `test_failed_removal_keeps_card_and_link` | 4.86 | passed |
| `test_lrv_attachment_activation_sync_and_removal` | 5.13 | passed |
| `test_remove_link_preserves_file_other_links_and_other_cards` | 8.45 | passed |
| `test_sync_buttons_work_in_either_order_and_show_the_sync_marker_until_done` | 8.78 | passed |
| `test_sync_updates_only_matching_video_and_refreshes_activation_offset` | 20.09 | passed |
| `test_video_context_menu_identifies_clicked_file` | 11.81 | passed |

### `test_video_decoder.CalibrationTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_malformed_protobuf_rejected` | 0.00 | passed |
| `test_native_principal_points_and_track_order` | 0.03 | passed |
| `test_trailer_directory_and_duplicate_identity` | 0.33 | passed |

### `test_video_decoder.DecodeTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_direct_decode_bounded_buffers_audio_and_seek` | 3.19 | passed |
| `test_invalid_file_reports_error` | 0.57 | passed |
| `test_lrv_preview_tracks_audio_and_seek` | 4.26 | passed |
| `test_packed_lens_split_preserves_luma_chroma_and_timestamps` | 0.00 | passed |
| `test_packed_x6_lrv_decodes_two_lenses_and_seeks` | 0.77 | passed |
| `test_single_track_insv_still_rejected` | 0.17 | passed |
| `test_standard_mp4_decode_aspect_audio_and_seek` | 1.16 | passed |

### `test_video_decoder.PreviewSizeTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_flat_fit_rotation_and_source_limit` | 0.00 | passed |
| `test_high_dpi_respects_actual_render_resolution` | 0.00 | passed |
| `test_insv_zoom_and_native_limit` | 0.00 | passed |

### `setUpClass (test_video_gui.DesktopTests)`

| Test | Seconds | Result |
| --- | ---: | --- |
| `DesktopTests)` | 0.00 | skipped |

### `test_workspace_file.CardStateTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_3d_plot_keeps_traces_camera_and_sample_limit` | 1.60 | passed |
| `test_frequency_keeps_fft_settings_peak_line_and_view` | 1.41 | passed |
| `test_sample_interval_card_keeps_its_series` | 1.34 | passed |

### `test_workspace_file.MapViewTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_reported_camera_is_saved` | 0.00 | passed |
| `test_saved_view_is_sent_with_the_first_tracks` | 0.01 | passed |

### `test_workspace_file.WorkspaceFileFormatTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_other_files_are_refused` | 0.07 | passed |
| `test_paths_are_stored_relative_and_read_back_absolute` | 0.07 | passed |
| `test_recent_list_keeps_ten_newest_without_duplicates` | 0.02 | passed |

### `test_workspace_file.WorkspaceMenuTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_save_open_and_recent_list_restore_navigator_and_cards` | 40.70 | passed |

### `test_workspace_file.WorkspaceRestoreTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_cards_rows_views_colors_and_markers_survive_a_round_trip` | 7.62 | passed |
| `test_missing_files_are_reported_and_other_cards_open` | 1.23 | passed |
| `test_opening_replaces_existing_cards` | 1.28 | passed |

### `test_xyz_plot.XYZCardTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_grouped_legend_replaces_and_swaps_axes_across_traces` | 1.11 | passed |
| `test_legend_context_menu_switches_values_and_time` | 0.41 | passed |
| `test_loading_coalesces_requests_and_shutdown_does_not_wait` | 0.04 | passed |
| `test_menu_offers_partial_3d_and_can_append_to_existing_card` | 0.56 | passed |
| `test_mouse_drag_rotates_real_view_camera` | 0.03 | passed |
| `test_multiple_traces_time_assignment_camera_range_and_removal` | 0.84 | passed |
| `test_render_keeps_unix_time_ticks_distinct_and_pads_constant_axes` | 0.63 | passed |
| `test_time_axis_drop_preserves_component_and_rejects_ordinary_cards` | 1.53 | passed |

### `test_xyz_plot.XYZDataTests`

| Test | Seconds | Result |
| --- | ---: | --- |
| `test_aligns_different_rates_and_units_without_extrapolation` | 0.16 | passed |
| `test_bounded_sampling_range_and_chunk_boundary_alignment` | 0.23 | passed |
| `test_nonfinite_values_preserve_breaks_and_invalid_inputs_report_errors` | 0.36 | passed |
| `test_time_axis_and_absolute_cross_file_alignment` | 0.24 | passed |
| `test_xy_alignment_allows_repeated_nearest_samples` | 0.13 | passed |
