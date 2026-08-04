#!/usr/bin/env python3
"""
OPIR_JSON_BUILDER.py

Place this file in the repository root and run:

    python OPIR_JSON_BUILDER.py
    python OPIR_JSON_BUILDER.py "configs\\experiments\\existing_config.json"

Standard-library-only split-screen JSON editor and OPIR configuration guide.

The top search field scans configs/experiments for existing JSON files.
Hovering over a top-level JSON section highlights the entire section.
Clicking a section selects it and opens a repository-grounded profile on the right.
Profiles recursively list every nested field, accepted finite values, constraints, and current values.
Repository descriptions are embedded so startup does not scan the full source tree.

Startup template precedence:
1. Positional command-line JSON path.
2. --template JSON path.
3. DEFAULT_TEMPLATE_JSON near the top of this file.
4. Built-in CPU template when the configured file does not exist.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any


APP_TITLE = "OPIR JSON Configuration Builder"

# Set this to a working JSON path relative to the repository root.
# Leave it as None to start with the built-in CPU template.
DEFAULT_TEMPLATE_JSON = (
    "configs/experiments/"
    "mt1024_single_comprehensive_output_cloud_front_soft_n32_w16_CPU_ONLY.json"
)

CONFIG_DIR = Path("configs") / "experiments"
VENV_NAMES = ("opir-aberration-gpu-env", ".venv", "venv")


HELP: dict[str, dict[str, Any]] = {
    "root": {
        "title": "Configuration overview",
        "summary": (
            "The JSON controls optical response construction, target and background generation, "
            "matched filtering, candidate selection, tracking, event qualification, output policy, "
            "and optional sweeps. Use one value per setting for a single run. Use a sweep block or "
            "several explicit cases when comparing operating conditions."
        ),
        "fields": {
            "description": ("Human-readable purpose of the configuration.", '"CPU soft-cloud validation"'),
            "config_revision": ("Version label for the configuration.", '"cpu_single_v1"'),
            "random_seed": ("Reproducible random seed.", "12345"),
        },
        "single": {"description": "Single CPU validation", "config_revision": "cpu_single_v1", "random_seed": 12345},
        "sweep": {"description": "SCNR/background sweep", "config_revision": "sweep_v1", "random_seed": 12345},
    },
    "output": {
        "title": "Output folder and run name",
        "summary": "Defines the parent output folder and base name of the timestamped run directory.",
        "fields": {
            "root": ("Parent output folder relative to the repository.", '"outputs/mtv"'),
            "run_name": ("Base name of the run folder.", '"cloud_front_cpu_single"'),
            "handoff_subfolder": ("Compact result-package folder.", '"handoff_outputs"'),
        },
        "single": {"root": "outputs/mtv", "run_name": "cloud_front_cpu_single", "handoff_subfolder": "handoff_outputs"},
        "sweep": {"root": "outputs/mtv", "run_name": "scnr_background_sweep", "handoff_subfolder": "handoff_outputs"},
    },
    "output_policy": {
        "title": "Output policy",
        "summary": (
            "Choose one compact compressed result or retain the ordinary CSV, JSON, figure, timing, "
            "localization, and handoff outputs."
        ),
        "fields": {
            "enabled": ("True enables single-comprehensive-output mode.", "true or false"),
            "mode": ("Output behavior label.", '"single_comprehensive_file" or "development_full"'),
            "filename": ("Compressed comprehensive output filename.", '"comparative_detection_tracking_metrics.json.gz"'),
            "cleanup_intermediate_outputs": ("Delete ordinary successful-run outputs.", "true or false"),
            "retain_intermediates_on_failure": ("Keep partial output after failure.", "true"),
            "compression_level": ("Gzip compression level.", "1 through 9"),
        },
        "single": {
            "enabled": True,
            "mode": "single_comprehensive_file",
            "filename": "comparative_detection_tracking_metrics.json.gz",
            "compression_level": 6,
            "cleanup_intermediate_outputs": True,
            "retain_intermediates_on_failure": True,
        },
        "sweep": {
            "enabled": False,
            "mode": "development_full",
            "cleanup_intermediate_outputs": False,
            "retain_intermediates_on_failure": True,
        },
    },
    "optics": {
        "title": "Optical PSF/PRF source",
        "summary": (
            "Select generated explicit-Zernike profiles, external arrays, or Zemax/Huygens data. "
            "HDF5 PRF files are listed under external_prfs with a path and internal dataset key."
        ),
        "fields": {
            "prf_source": ("PRF construction method.", '"generated_explicit_profiles", "external", or "zemax"'),
            "explicit_zernike_profiles_file": ("Named Zernike profile bank.", '"configs/aberrations/explicit_zernike_profile_bank_13.json"'),
            "explicit_profile_selection": ("Selected profile IDs.", '["center", "left_edge", "right_edge"]'),
            "external_prfs": ("External PRF/PSF definitions.", '[{"name":"center","path":"datasets/optical_prfs/center.h5","hdf5_dataset":"prf"}]'),
        },
        "single": {
            "prf_source": "generated_explicit_profiles",
            "explicit_zernike_profiles_file": "configs/aberrations/explicit_zernike_profile_bank_13.json",
            "explicit_profile_selection": ["center", "left_edge", "right_edge"],
        },
        "sweep": {
            "prf_source": "generated_explicit_profiles",
            "explicit_zernike_profiles_file": "configs/aberrations/explicit_zernike_profile_bank_13.json",
            "explicit_profile_selection": ["center", "left_edge", "right_edge"],
        },
    },
    "templates": {
        "title": "Matched-filter templates",
        "summary": (
            "Controls template crop size and subpixel phases. Final filter count is approximately "
            "base PRFs multiplied by centroid offsets."
        ),
        "fields": {
            "template_size": ("Odd square template side length.", "17, 25, 33, 41, or 49"),
            "centroid_offsets_yx": ("Subpixel [y,x] offsets.", "[[-0.5,0.0],[0.0,0.0],[0.5,0.0]]"),
            "zero_mean": ("Remove template mean.", "true"),
            "unit_norm": ("Normalize template L2 norm.", "true"),
        },
        "single": {
            "template_size": 33,
            "centroid_offsets_yx": [[-0.5, 0.0], [0.0, 0.0], [0.5, 0.0]],
            "zero_mean": True,
            "unit_norm": True,
        },
        "sweep": {
            "template_size": 33,
            "centroid_offsets_yx": [
                [-0.5, -0.5], [-0.5, 0.0], [-0.5, 0.5],
                [0.0, -0.5], [0.0, 0.0], [0.0, 0.5],
                [0.5, -0.5], [0.5, 0.0], [0.5, 0.5],
            ],
            "zero_mean": True,
            "unit_norm": True,
        },
    },
    "scnr_l2_values": {
        "title": "Target strength",
        "summary": "SCNR_L2 controls target strength relative to clutter plus noise.",
        "fields": {"scnr_l2_values": ("One or more target-strength values.", "[2.75] or [1.5,2.0,2.5,3.0,3.5,4.0]")},
        "single": [2.75],
        "sweep": [1.5, 2.0, 2.5, 2.75, 3.0, 3.5, 4.0],
    },
    "temporal_background": {
        "title": "Temporal background",
        "summary": "Selects scene/clutter generation and optional frame-to-frame motion.",
        "fields": {
            "preset": ("Named background preset.", '"satellite_visual_cloud_front_soft"'),
            "dynamic": ("Use changing background frames.", "true or false"),
            "cloud_velocity_yx": ("Cloud motion in pixels/frame.", "[0.25,0.5]"),
        },
        "single": {"preset": "satellite_visual_cloud_front_soft", "dynamic": True},
        "sweep": {
            "preset": "satellite_visual_cloud_front_soft",
            "dynamic": True,
            "_sweep_candidates": [
                "satellite_visual_uniform_sensor",
                "satellite_visual_cloud_front_soft",
                "opir_multilayer_clouds",
            ],
        },
    },
    "target_program": {
        "title": "Programmed targets",
        "summary": "Defines deterministic target identity, shape, initial position, velocity, source PRF, and strength.",
        "fields": {
            "enabled": ("Use explicit target definitions.", "true"),
            "targets": ("List of targets.", '[{"target_id":0,"start_yx":[256,256],"velocity_yx":[0.5,1.0],"source_prf_profile":"center"}]'),
        },
        "single": {
            "enabled": True,
            "targets": [{
                "target_id": 0,
                "shape": "disk",
                "radius_pixels": 1.0,
                "start_yx": [256.0, 256.0],
                "velocity_yx": [0.5, 1.0],
                "source_prf_profile": "center",
                "scnr_l2": 2.75,
            }],
        },
        "sweep": {
            "enabled": True,
            "targets": [{
                "target_id": 0,
                "shape": "disk",
                "start_yx": [256.0, 256.0],
                "velocity_yx": [0.5, 1.0],
                "source_prf_profile": "center",
            }],
        },
    },
    "tiling": {
        "title": "Tiling and matched filtering",
        "summary": (
            "Controls tile geometry, overlap, candidate limits, and CPU/GPU matched-filter execution. "
            "The tracking.tiling block may override this section."
        ),
        "fields": {
            "tile_size": ("Tile side length.", "128, 256, or 512"),
            "overlap": ("Overlap between adjacent tiles.", "16, 32, or 64"),
            "matched_filter_backend": ("Matched-filter implementation.", '"scipy_cpu" or "torch_cuda"'),
            "cuda_device": ("Requested device.", '"cpu" or "cuda"'),
            "cuda_filter_batch_size": ("Filters per GPU batch.", "1, 4, or 8"),
            "max_candidates_per_tile": ("Tile candidate cap.", "25, 50, or 100"),
            "max_candidates_per_frame": ("Post-merge frame cap.", "50, 100, 200, or 400"),
        },
        "single": {
            "enabled": True,
            "tile_size": 256,
            "overlap": 32,
            "matched_filter_backend": "scipy_cpu",
            "cuda_device": "cpu",
            "cuda_filter_batch_size": 1,
            "max_candidates_per_tile": 50,
            "max_candidates_per_frame": 100,
        },
        "sweep": {
            "enabled": True,
            "tile_size": 256,
            "overlap": 32,
            "matched_filter_backend": "scipy_cpu",
            "cuda_device": "cpu",
            "cuda_filter_batch_size": 1,
            "max_candidates_per_tile": 50,
            "max_candidates_per_frame": 200,
        },
    },
    "candidate_generation": {
        "title": "Candidate generation and CFAR",
        "summary": "Converts response maps into retained candidate detections.",
        "fields": {
            "method": ("Candidate extraction method.", '"positive_local_z", "cfar", or "adaptive_cfar"'),
            "local_z_threshold": ("Minimum standardized response.", "2.5, 3.0, 3.5, or 4.0"),
            "cfar_method": ("CFAR estimator.", '"ca", "go", "so", or "os"'),
            "training_cells": ("Background training cells.", "8, 12, 16, or 24"),
            "guard_cells": ("Cells excluded around target.", "2, 3, or 4"),
            "threshold_multiplier": ("Local threshold multiplier.", "2.5 to 4.5"),
        },
        "single": {"method": "positive_local_z", "local_z_threshold": 3.0},
        "sweep": {"method": "positive_local_z", "local_z_threshold": 3.0, "_candidate_thresholds": [2.5, 3.0, 3.5, 4.0]},
    },
    "tracking": {
        "title": "Multi-frame tracking",
        "summary": "Controls association, support, misses, motion consistency, confirmation, and worker count.",
        "fields": {
            "num_frames": ("Frames in each sequence.", "4, 6, 8, 12, or 16"),
            "workers": ("Track-level process count.", "4, 8, or 16"),
            "minimum_support_frames": ("Minimum supporting frames.", "2, 3, 4, or 5"),
            "minimum_support_fraction": ("Minimum support fraction.", "0.25 to 0.75"),
            "maximum_consecutive_misses": ("Allowed missed frames.", "0, 1, or 2"),
            "association_radius_pixels": ("Maximum association distance.", "2, 4, 6, or 8"),
        },
        "single": {
            "num_frames": 8,
            "workers": 8,
            "minimum_support_frames": 3,
            "minimum_support_fraction": 0.375,
            "maximum_consecutive_misses": 1,
            "association_radius_pixels": 6.0,
        },
        "sweep": {
            "num_frames": 8,
            "workers": 8,
            "minimum_support_frames": 3,
            "minimum_support_fraction": 0.375,
            "maximum_consecutive_misses": 1,
            "association_radius_pixels": 6.0,
        },
    },
    "operational_event_gate": {
        "title": "Operational event gate",
        "summary": "Converts retained tracks into final event declarations using hard gates and a score threshold.",
        "fields": {
            "enabled": ("Apply event qualification.", "true"),
            "mode": ("Threshold/calibration mode.", '"empirical", "frozen", or "development"'),
            "requested_pfa": ("Requested false-alarm probability.", "0.10, 0.05, 0.02, or 0.01"),
            "threshold": ("Fixed threshold for frozen mode.", "numeric"),
        },
        "single": {"enabled": True, "mode": "empirical", "requested_pfa": 0.05},
        "sweep": {"enabled": True, "mode": "empirical", "requested_pfa": 0.05, "_pfa_candidates": [0.1, 0.05, 0.02, 0.01]},
    },
    "reporting": {
        "title": "Reports and figures",
        "summary": "Controls score tables, figure generation, source-family comparisons, and scene examples.",
        "fields": {
            "enabled": ("Enable reporting.", "true"),
            "primary_track_scores": ("Scores emphasized in reports.", '["operational_event_score","localization_rank_score"]'),
            "primary_pfa_for_ranking": ("PFA used for front-page ranking.", "0.05"),
            "hide_empty_scores": ("Hide unavailable score families.", "true"),
            "figures.enabled": ("Enable ordinary figures.", "true or false"),
        },
        "single": {
            "enabled": True,
            "primary_track_scores": ["operational_event_score", "localization_rank_score"],
            "primary_pfa_for_ranking": 0.05,
            "hide_empty_scores": True,
            "figures": {
                "enabled": False,
                "include_sweep_figures": False,
                "include_standard_score_figures": False,
                "include_multitarget_figures": False,
                "include_zemax_comparison": False,
            },
        },
        "sweep": {
            "enabled": True,
            "primary_track_scores": ["operational_event_score", "localization_rank_score"],
            "primary_pfa_for_ranking": 0.05,
            "hide_empty_scores": True,
            "figures": {
                "enabled": True,
                "include_sweep_figures": True,
                "include_standard_score_figures": True,
                "include_multitarget_figures": True,
                "include_zemax_comparison": False,
            },
        },
    },
    "profiling": {
        "title": "Profiling",
        "summary": "Controls timing, candidate-cap, truth-retention, and backend diagnostic exports.",
        "fields": {
            "save_stage_timings": ("Export stage timing tables.", "true"),
            "save_candidate_cap_summary": ("Export cap saturation data.", "true"),
            "save_truth_retention_summary": ("Export target survival stages.", "true"),
            "save_cuda_timing_summary": ("Export CUDA timings.", "false for CPU"),
        },
        "single": {
            "enabled": True,
            "save_stage_timings": True,
            "save_candidate_cap_summary": True,
            "save_truth_retention_summary": True,
            "save_cuda_timing_summary": False,
        },
        "sweep": {
            "enabled": True,
            "save_stage_timings": True,
            "save_candidate_cap_summary": True,
            "save_truth_retention_summary": True,
            "save_cuda_timing_summary": False,
        },
    },
    "diagnostics": {
        "title": "Diagnostics",
        "summary": "Tabular candidate diagnostics are useful; raw response maps, frames, GIFs, and MP4s can be very large.",
        "fields": {
            "export_candidate_rank_diagnostics": ("Save candidate-rank rows.", "true"),
            "candidate_rank_export_top_k": ("Candidates exported per stage.", "20, 30, or 50"),
            "export_debug_visualization": ("Enable debug images.", "false for normal analytical runs"),
            "max_debug_sequences": ("Sequences with debug media.", "0, 1, or 2"),
        },
        "single": {
            "export_candidate_rank_diagnostics": True,
            "candidate_rank_export_top_k": 30,
            "export_debug_visualization": False,
            "max_debug_sequences": 0,
        },
        "sweep": {
            "export_candidate_rank_diagnostics": True,
            "candidate_rank_export_top_k": 30,
            "export_debug_visualization": False,
            "max_debug_sequences": 0,
        },
    },
    "sweep": {
        "title": "Parameter sweep",
        "summary": (
            "Enable several related cases. Exact accepted syntax can differ between repository revisions; "
            "copy the structure from a known working sweep configuration when available."
        ),
        "fields": {
            "enabled": ("Enable multi-case evaluation.", "true or false"),
            "mode": ("Sweep construction method.", '"grid", "cases", or "random"'),
            "parameters": ("JSON-path values used for a grid.", '{"scnr_l2_values":[[2.0],[2.5],[3.0]]}'),
            "cases": ("Explicit named override cases.", '[{"name":"case_1","overrides":{"scnr_l2_values":[2.5]}}]'),
        },
        "single": {"enabled": False, "mode": "cases", "cases": []},
        "sweep": {
            "enabled": True,
            "mode": "grid",
            "parameters": {
                "scnr_l2_values": [[1.5], [2.0], [2.5], [3.0], [3.5], [4.0]],
                "temporal_background.preset": [
                    "satellite_visual_uniform_sensor",
                    "satellite_visual_cloud_front_soft",
                    "opir_multilayer_clouds",
                ],
            },
        },
    },
    "external_sequence_dataset": {
        "title": "External HDF5 sequences",
        "summary": (
            "Recommended schema for an external-sequence adapter. The ordinary synthetic runner only uses "
            "this section when the repository contains code that explicitly reads it."
        ),
        "fields": {
            "dataset_root": ("Folder containing sequence data.", '"datasets/external_sequences/experiment_001"'),
            "manifest": ("Sequence manifest.", '"datasets/external_sequences/experiment_001/manifest.csv"'),
            "frame_dataset": ("Internal HDF5 frame key.", '"frames/noisy"'),
            "truth_position_dataset": ("Internal truth-position key.", '"truth/positions_yx"'),
            "axis_order": ("Array axis order.", '"THW", "HWT", or "THWC"'),
            "channel": ("Selected channel for multichannel arrays.", "0 or null"),
        },
        "single": {
            "enabled": True,
            "format": "hdf5",
            "dataset_root": "datasets/external_sequences/experiment_001",
            "manifest": "datasets/external_sequences/experiment_001/manifest.csv",
            "frame_dataset": "frames/noisy",
            "truth_position_dataset": "truth/positions_yx",
            "truth_velocity_dataset": "truth/velocity_yx",
            "axis_order": "THW",
            "channel": None,
        },
        "sweep": {
            "enabled": True,
            "format": "hdf5",
            "dataset_root": "datasets/external_sequences/experiment_001",
            "manifest": "datasets/external_sequences/experiment_001/manifest.csv",
            "frame_dataset": "frames/noisy",
            "axis_order": "THW",
        },
    },
}


COMPLETE_SECTION_SUMMARIES = {'description': 'Human-readable statement of the experiment’s purpose, scale, background, execution mode, and provenance goals.', 'config_revision': 'Stable revision identifier for distinguishing this JSON from earlier or later configuration variants.', 'random_seed': 'Base pseudo-random seed used to reproduce generated backgrounds, noise realizations, target placement, and paired comparisons.', 'output': 'Defines the parent output directory, timestamped run-name prefix, and compact handoff-output subfolder.', 'output_policy': 'Controls whether successful runs retain all ordinary outputs or consolidate them into one validated compressed comprehensive result.', 'single_output_smoke_test': 'Declares the expected schema, file count, sequence counts, filter-bank dimensions, and required result sections used to validate single-output behavior.', 'benchmark': 'Sets strict operational-benchmark rules, including fallback prohibitions, truth-isolation requirements, module-failure behavior, and temporal-background requirements.', 'velocity_search': 'Controls how candidate target velocities are generated and whether truth velocity or truth-neighbor hypotheses may enter the operational search.', 'statistics': 'Controls paired-randomization and resampling assumptions used when comparing methods and estimating uncertainty.', 'sweep': 'Defines the principal input metric, SCNR operating points, and number of trials evaluated at each point.', 'significance': 'Sets bootstrap confidence parameters and target performance thresholds used to interpret whether the run meets its intended validation goals.', 'metrics': 'Lists the false-alarm probabilities at which detection and tracking performance should be reported.', 'run_plan': 'Documents the intended number of signal/noise tasks, targets, frames, workers, and total workload for this specific run.', 'parallel_execution': 'Describes the outer execution layout and the intended relationship among orchestrator, track workers, tile workers, and total task count.', 'parameter_sweep': 'Records whether this file is a single case or a parameter sweep, which variables are fixed or controlled, and what cases should be generated.', 'scene': 'Defines the global image dimensions and target-safe margins used by generation, tiling, tracking, and truth validation.', 'backgrounds': 'Supplies the primary background preset and duplicate geometry fields used by legacy or compatibility paths.', 'background': 'Provides a compact background identity used by components that expect a singular background block.', 'temporal_background': 'Controls generation of the time-varying background sequence, including preset, noise scale, cloud motion, and optional background-frame export.', 'optics': 'Selects the optical PRF source and the explicit Zernike profiles or external optical data used to inject targets and construct matched filters.', 'templates': 'Controls matched-filter crop size, fallback Gaussian widths, and subpixel centroid offsets used to expand each base PRF into several templates.', 'zemax_filter_bank': 'Controls optional Zemax/Huygens or Zernike-derived filter construction, manifest interpretation, grouping, weighting, and production-use caveats.', 'target': 'Provides legacy or shared default target geometry used when a more explicit target program does not override it.', 'target_program': 'Defines authoritative programmed targets, including shape, optical appearance, SCNR, initial position, trajectory, separation constraints, and boundary behavior.', 'multi_target': 'Controls multi-target truth generation and evaluation, assignment method, separation requirements, exported assignment data, and fallback behavior.', 'methods': 'Selects which detector or filtering methods are enabled for the comparative run.', 'detection': 'Controls single-frame preprocessing, peak extraction, optional response CFAR, subpixel refinement, ranking, and retained-candidate behavior.', 'classical_filter_preprocessing': 'Defines the preprocessing applied before classical matched filtering, including background subtraction, local normalization, bandpass filtering, and clipping.', 'whitening': 'Controls power-spectral-density estimation and numerical flooring used to whiten image and filter spectra.', 'response_cfar': 'Defines the reusable response-map CFAR settings, including estimator type, guard/training cells, threshold scale, score mode, and computational backend.', 'signature': 'Controls the number of strongest signature or response features retained for downstream comparison or diagnostics.', 'tiling': 'Controls tiled matched-filter processing, tile overlap, candidate caps, duplicate merging, subpixel preservation, source-family quotas, and CPU/GPU backend selection.', 'tracking': 'Contains the primary multi-frame operational tracking configuration, including candidate generation, motion hypotheses, association, persistence, pruning, ranking, and nested tiling.', 'motion_gated_tracker': 'Defines the standalone motion-gated track filter and ranking weights used to reject implausible trajectories and retain the strongest motion-consistent tracks.', 'localization_scoring': 'Defines the truth-independent localization ranking score, its component weights, coordinate policy, and where it is computed in the pruning pipeline.', 'operational_event_gate': 'Converts retained tracks into final operational events using hard support/motion gates and a weighted event score or calibrated threshold.', 'low_pfa_covariance': 'Controls robust local covariance/variance estimation and gradient suppression used to stabilize low-false-alarm scoring.', 'evt': 'Controls optional single-frame extreme-value tail fitting for estimating thresholds below the direct empirical false-alarm resolution.', 'track_level_evt': 'Controls empirical or EVT-style track-level threshold estimation, score families, target PFA values, regional shrinkage, and no-detection floors.', 'sequence_event_detection': 'Controls the optional dense sequence-event branch and records why it may be disabled for sparse candidate-only tiling.', 'tracking_validation': 'Controls the legacy or diagnostic tracking-validation branch and whether it reuses generated backgrounds or stitched maps.', 'track_level_validation': 'Enables the primary signal/noise track-level validation path and sets its worker count, chunking, and operational role.', 'parallel': 'Legacy/general multiprocessing controls. In this CPU configuration it is intentionally disabled to avoid nested parallelism.', 'execution': 'Top-level execution switch and outer worker settings used by the experiment orchestrator.', 'track_parallel': 'Defines the actual track-sequence multiprocessing plan, including worker count, task count, chunk size, and expected CUDA context count.', 'ml': 'Controls the optional learned model path, device, normalization, response output, and missing-model behavior.', 'profiling': 'Selects stage timing, candidate-cap, truth-retention, backend-resolution, tile-substage, and fallback diagnostic exports.', 'reporting': 'Selects primary and diagnostic score families, ranking PFA, figure generation, scene examples, and report-only interpretation notes.', 'interpretation_outputs': 'Declares which files must remain after a compact successful run and which ordinary/debug outputs are intentionally excluded.', 'progress': 'Controls terminal progress output for trials, bootstrap iterations, and per-SCNR summaries.', 'visualization': 'Controls ordinary debug-frame, response-map, candidate-map, overlay, GIF, and MP4 output.', 'debug_visualization': 'Controls a second, explicitly diagnostic visualization path and which target/candidate/track annotations appear.', 'diagnostics': 'Collects operational notes, worker recommendations, candidate-rank export controls, expected reporting files, and known path/debug limitations.', 'config_compatibility': 'Documents compatibility expectations, duplicated aliases, authoritative sections, and fields retained for older code paths.', 'single_output_validation': 'Defines post-run checks that verify the comprehensive output exists, matches the required schema, and contains required sections and filter provenance.', 'cpu_execution': 'Documents CPU-only execution assumptions, backend/device requirements, process/thread limits, and expected performance or memory behavior.'}

FIELD_TERM_DESCRIPTIONS = {
    "enabled": "Turns this feature or processing branch on or off.",
    "mode": "Selects the operational strategy or implementation variant.",
    "name": "Human-readable or stable identifier for this item.",
    "purpose": "Explains why this block or case exists.",
    "note": "Documents intended behavior, assumptions, or a known limitation.",
    "notes": "Collection of explanatory notes; these generally document rather than change execution.",
    "path": "File-system location, normally interpreted relative to the repository root.",
    "file": "File name or path consumed or produced by this component.",
    "root": "Parent directory used to resolve related files.",
    "manifest": "Table that lists external data files and associated metadata.",
    "preset": "Named preconfigured operating condition.",
    "workers": "Number of worker processes used at this execution level.",
    "num_workers": "Compatibility alias for the worker-process count.",
    "chunksize": "Number of tasks assigned to a worker per scheduling chunk.",
    "parallel": "Whether this level of multiprocessing is active.",
    "size": "Spatial size or count used by the owning section.",
    "image_size": "Square image side length in pixels.",
    "image_shape": "Image dimensions, normally [height, width].",
    "image_height": "Image height in pixels.",
    "image_width": "Image width in pixels.",
    "num_frames": "Number of temporal frames in each sequence.",
    "num_sequences": "Number of sequences generated or analyzed.",
    "signal_sequences": "Number of target-present sequences.",
    "noise_sequences": "Number of target-absent sequences.",
    "scnr": "Signal-to-clutter-plus-noise ratio setting.",
    "pfa": "Probability-of-false-alarm operating point.",
    "alpha": "Statistical significance level used for confidence intervals or tests.",
    "threshold": "Decision threshold applied to a score or local statistic.",
    "threshold_scale": "Multiplier applied to an estimated local noise or clutter level.",
    "weight": "Relative contribution of this term to a combined score.",
    "fraction": "Normalized proportion between zero and one.",
    "min": "Lower admissible bound or minimum required value.",
    "max": "Upper admissible bound or maximum retained value.",
    "top_k": "Number of highest-ranked items retained.",
    "radius": "Spatial distance or neighborhood radius in pixels unless otherwise stated.",
    "sigma": "Gaussian scale or standard-deviation-like parameter.",
    "velocity": "Target or background motion in pixels per frame, generally in [y, x] order.",
    "acceleration": "Maximum or weighted change in velocity between frames.",
    "support": "Temporal or spatial evidence required to retain a candidate or track.",
    "streak": "Longest consecutive sequence of supporting detections.",
    "dominance": "Fraction of total evidence contributed by the strongest frame.",
    "prediction_error": "Difference between predicted and observed track locations.",
    "backend": "Numerical implementation used for this operation.",
    "device": "Requested processing device, such as CPU or CUDA.",
    "tile": "Parameter controlling spatial tiled processing.",
    "candidate": "Parameter controlling candidate creation, ranking, retention, or export.",
    "track": "Parameter controlling track generation, scoring, pruning, or reporting.",
    "truth": "Ground-truth information used for generation or evaluation; strict operational paths should not use it for selection.",
    "export": "Whether or how this data product is written to the run directory.",
    "save": "Whether this diagnostic or output artifact is written.",
    "include": "Whether this item is included in construction, reporting, or output.",
    "allow": "Permission for the stated fallback or behavior.",
    "require": "Mandatory condition that must be satisfied.",
    "fallback": "Alternative used when the preferred source or method is unavailable.",
    "score": "Named response, ranking, or decision statistic.",
    "key": "Dictionary or column name used to locate a score or data product.",
    "source_family": "Filter or PRF provenance family used for attribution and quota preservation.",
    "filter": "Matched-filter construction, selection, attribution, or reporting parameter.",
    "prf": "Point-response-function identity or source.",
    "psf": "Point-spread-function identity or source.",
    "zernike": "Zernike coefficient, basis, source file, or synthesized-wavefront control.",
    "focus": "Optical focus or defocus metadata.",
    "wavelength": "Optical wavelength metadata.",
    "coordinate": "Coordinate convention or policy.",
    "subpixel": "Fractional-pixel localization or template-offset setting.",
    "normalize": "Whether values are rescaled to a standard normalization.",
    "clip": "Bound applied to prevent extreme values from dominating.",
    "floor": "Numerical lower bound used for stability.",
    "method": "Algorithm selected for the owning operation.",
    "expected": "Validation expectation; normally used for checks rather than detector behavior.",
    "required": "Items that must exist or pass validation.",
    "excluded": "Outputs intentionally omitted from the final retained set.",
}


def _compact_value(value, max_chars=260):
    rendered = json.dumps(value, ensure_ascii=False)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[: max_chars - 3] + "..."


def _field_description(field_name):
    normalized = str(field_name).lower()
    matches = []
    for term, description in FIELD_TERM_DESCRIPTIONS.items():
        if term in normalized:
            matches.append(description)
    if matches:
        unique = []
        for item in matches:
            if item not in unique:
                unique.append(item)
        return " ".join(unique[:2])
    readable = normalized.replace("_", " ")
    return f"Controls or documents the {readable} setting used by this section."


def _section_value_from_editor(editor_text, key):
    try:
        parsed = json.loads(editor_text)
    except Exception:
        return None
    if isinstance(parsed, dict):
        return parsed.get(key)
    return None


def _dynamic_section_profile(key, editor_text):
    canonical = ALIASES.get(key, key)
    value = _section_value_from_editor(editor_text, key)
    if value is None and canonical != key:
        value = _section_value_from_editor(editor_text, canonical)

    summary = COMPLETE_SECTION_SUMMARIES.get(
        key,
        COMPLETE_SECTION_SUMMARIES.get(
            canonical,
            f"Controls the {str(key).replace('_', ' ')} portion of the experiment configuration.",
        ),
    )

    fields = {}
    if isinstance(value, dict):
        for field_name, field_value in value.items():
            fields[str(field_name)] = (
                _field_description(field_name),
                _compact_value(field_value),
            )
    else:
        fields[str(key)] = (
            _field_description(key),
            _compact_value(value),
        )

    return {
        "title": str(key).replace("_", " ").title(),
        "summary": summary,
        "fields": fields,
        "single": value,
        "sweep": {
            "investigator_guidance": (
                "For a sweep, keep unrelated settings fixed and vary only the fields "
                "that test the stated hypothesis. Use explicit case names or the "
                "repository's working parameter_sweep structure."
            ),
            "current_section_as_starting_point": value,
        },
    }



# =============================================================================
# Repository-grounded help metadata
# =============================================================================

REFINED_SECTION_SUMMARIES = {
    **COMPLETE_SECTION_SUMMARIES,
    "scene": (
        "Defines image geometry and target-safe margins. The refactored track-level worker and canonical "
        "SequenceData preserve independent height and width. The ordinary synthetic run_snr_auc_sweep "
        "path remains validated as square-only until every upstream generator is migrated, while cached "
        "and external sequence paths may use rectangular H×W arrays."
    ),
    "backgrounds": (
        "Selects the background realism preset and optional parameter overrides. The supplied "
        "background generators accept a single size argument and produce size×size synthetic arrays. "
        "The finite preset names are read from src/backgrounds/presets.py when the builder starts."
    ),
    "background": (
        "Compact compatibility alias for the selected background. The main synthetic background "
        "helper reads backgrounds.preset; this singular block may be documentary or consumed by "
        "newer/alternate code paths."
    ),
    "temporal_background": (
        "Controls the centralized track-level temporal-background generator, including static, independent, "
        "preset-sequence, advected, and evolving modes, background preset, noise scale, cloud velocity, "
        "strict fallback policy, and optional frame export."
    ),
    "optics": (
        "Controls construction of the field-dependent point-response-function library. The active prf_setup.py "
        "supports generated, generated_explicit_profiles, explicit_zernike, external, Zemax, CODE V, measured, "
        "NPY/NPZ/text, and HDF5-backed PRF sources with provenance metadata."
    ),
    "templates": (
        "Controls cropping and centroid-phase expansion of PRFs into matched-filter templates. "
        "template_size is used as a square crop size; centroid_offsets is a list of [y, x] subpixel "
        "shifts. More offsets improve sampling coverage but increase filter-bank cost proportionally."
    ),
    "zemax_filter_bank": (
        "Controls optional Zemax-derived Huygens PSF, direct PRF, Zernike metadata, or approximate "
        "Zernike-wavefront sources. mode is source-backed as append or replace. Huygens PSF exports "
        "are the preferred production source; the repository documents exact annular-Zernike "
        "normalization as incomplete for synthesized wavefront PSFs."
    ),
    "target_program": (
        "Defines explicit target identities, shapes, optical appearances, starts, and trajectories. The active "
        "track-level sequence pipeline calls target_program_enabled(), resolve_target_program(), and trajectory "
        "manifest export before injection and multi-target assignment."
    ),
    "multi_target": (
        "Controls legacy/random multi-target generation and evaluation: target count, separation, "
        "velocity branches, crossing policy, and truth-to-track assignment. The repository supports "
        "greedy assignment and optional Hungarian assignment when SciPy is available."
    ),
    "detection": (
        "Controls classical single-frame detection: preprocessing, detector response, candidate "
        "extraction, optional CFAR, subpixel refinement, and ranked-candidate retention. Source-backed "
        "detector modes are raw, gaussian, matched, and filter_bank; candidate modes are topk, cfar, "
        "and adaptive_cfar."
    ),
    "classical_filter_preprocessing": (
        "Configuration convention for preprocessing before classical matched filtering. The supplied "
        "scoring path maps related settings into DetectionConfig/enhanced preprocessing, but the exact "
        "top-level key classical_filter_preprocessing was not found in the source dump."
    ),
    "whitening": (
        "Controls PSD-based matched-filter whitening. Source-backed PSD modes are radial and full_2d. "
        "The floor fraction prevents division by very small spectral estimates."
    ),
    "response_cfar": (
        "Controls compatibility response-map CFAR normalization. Supported methods are CA, GO, SO, and OS; "
        "supported score modes include raw, excess, cfar_excess, SNR, cfar_snr, and positive_excess. Keep this "
        "block synchronized with detection.response_cfar when both are present."
    ),
    "tiling": (
        "Controls overlapping tiled matched-filter execution, tile-local and frame-global candidate caps, "
        "deduplication, source-family preservation, and scipy_cpu or torch_cuda execution. The nested "
        "tracking.tiling block may override this section and must request the same backend/device."
    ),
    "tracking": (
        "Primary multi-frame configuration. It controls frame count, velocity hypotheses, adaptive "
        "candidate generation, local association, persistence, confirmation, pruning, motion gating, "
        "and optional sequence-level evidence integration. Some nested blocks in the JSON are newer "
        "than the supplied source dump; the profile distinguishes source-backed choices from "
        "configuration-only choices."
    ),
    "motion_gated_tracker": (
        "Filters and ranks tracks by speed, acceleration, prediction error, temporal support, "
        "straightness, velocity consistency, and evidence terms. Numeric weights are open-ended; "
        "larger positive weights increase the corresponding contribution or penalty."
    ),
    "localization_scoring": (
        "Defines the active truth-independent localization ranking score and coordinate-refinement policy. "
        "The track-level sequence, local association, pruning, motion-gated tracker, and trajectory-volume "
        "paths import and attach localization_rank_score."
    ),
    "operational_event_gate": (
        "Defines the active hard track-quality gates and final event score/threshold applied to retained tracks. "
        "Development mode is calibration-only; final operational claims require a frozen threshold validated on "
        "independent held-out sequences."
    ),
    "evt": (
        "Controls optional extreme-value threshold estimation. The supplied EVT implementation accepts "
        "empirical or gpd; the adaptive wrapper also treats empirical, quantile, and direct as empirical modes."
    ),
    "track_level_evt": (
        "Controls track-level signal/noise threshold estimation and requested PFA reporting. The adaptive "
        "threshold code accepts gpd and empirical-style aliases empirical, quantile, and direct."
    ),
    "methods": (
        "Selects single-frame comparison methods. Source-backed entries are raw, matched_true, "
        "matched_mismatch_gaussian, filter_bank, whitened_matched, signature_verified, and deep_detector."
    ),
    "ml": (
        "Controls the optional learned detector. Source-backed model aliases include small_cnn, "
        "small_cnn_heatmap, small, unet, unet_heatmap, and small_unet. Runner device examples are "
        "cpu, cuda, and mps."
    ),
    "output_policy": (
        "Controls the active compact-versus-development output path. In single_comprehensive_file mode the "
        "runner builds and validates one gzip JSON, then removes intermediates only after successful creation; "
        "failures retain intermediate outputs."
    ),
}


BACKGROUND_PRESET_FALLBACK = [
    "baseline",
    "cloud_edge_hard",
    "easy_clear",
    "high_false_alarm",
    "limb_gradient",
    "motion_smear",
    "satellite_atmospheric_realistic",
    "satellite_realistic",
    "satellite_visual_broken_cumulus",
    "satellite_visual_grain_stress",
    "satellite_visual_hard_cloud_edge",
    "satellite_visual_limb_haze",
    "satellite_visual_realistic",
    "satellite_visual_smooth_realistic",
]


# Exact path metadata. "verified" means the finite values were found in the
# supplied repository source. False means the values come from the uploaded
# configuration or a newer convention and should be checked against the
# installed code.
FIELD_METADATA = {
    "output.root": {
        "description": "Parent output directory. Relative paths are resolved from the process working directory, normally the repository root.",
        "constraints": "String path.",
    },
    "output.run_name": {
        "description": "Prefix used for the timestamped run directory.",
        "constraints": "Use a short Windows-safe name to reduce path-length problems.",
    },
    "output.handoff_subfolder": {
        "description": "Subdirectory intended to hold a compact handoff package.",
    },
    "output_policy.mode": {
        "description": "Selects compact single-file retention or a development-style full output tree.",
        "choices": ["single_comprehensive_file", "development_full"],
        "verified": True,
        "note": "The direct runner writes and validates the comprehensive gzip and removes intermediates only after success.",
    },
    "output_policy.compression_level": {
        "description": "Gzip compression level for the comprehensive output.",
        "constraints": "Integer 1–9; 6 is a balanced default.",
    },
    "sweep.input_metric": {
        "description": "Controls how target strength is interpreted by the sweep.",
        "choices": ["amplitude", "scnr", "scnr_l2"],
        "aliases": ["snr / legacy_snr → legacy amplitude behavior", "scnr-l2 → scnr_l2 in injection helpers"],
        "verified": True,
    },
    "sweep.scnr_values": {
        "description": "SCNR_L2 operating points evaluated by the sweep. A one-element list is a single operating point.",
        "constraints": "List of positive numbers.",
        "sweep": "Use coarse values first, then add points around the Pd/AUC transition.",
    },
    "sweep.trials_per_snr": {
        "description": "Independent signal/noise trial count per configured input-strength point.",
        "constraints": "Positive integer; larger values improve uncertainty estimates and increase runtime.",
    },
    "statistics.bootstrap_resampling": {
        "description": "Resampling unit used for confidence intervals.",
        "choices": ["paired_trial"],
        "verified": False,
    },
    "scene.image_size": {
        "description": "Authoritative side length for standard synthetic generation and the main track-level worker in the supplied repository.",
        "constraints": "Positive integer. Standard synthetic output is image_size × image_size.",
        "note": "This is the field actually read by dataset_generation.py and track_level_worker.py.",
    },
    "scene.size": {
        "description": "Compatibility alias for image_size. It does not independently define width in the supplied standard synthetic path.",
        "constraints": "Keep equal to scene.image_size unless a specific adapter documents otherwise.",
    },
    "scene.image_shape": {
        "description": "Descriptive [height, width] geometry. Many downstream array operations support H×W, but the standard synthetic generator does not use this field as its authoritative shape.",
        "constraints": "Two positive integers [H, W]. Rectangular values require an external/cached path that preserves the actual array shape.",
    },
    "scene.image_height": {
        "description": "Descriptive or compatibility image height. The standard synthetic generator still reads scene.image_size.",
        "constraints": "Positive integer; keep synchronized with image_shape[0].",
    },
    "scene.image_width": {
        "description": "Descriptive or cached-data image width. The cached adapter records it for diagnostics, while warning that many current paths assume square images.",
        "constraints": "Positive integer; keep synchronized with image_shape[1].",
    },
    "scene.target_margin_pixels": {
        "description": "Minimum border clearance used when sampling or validating target trajectories.",
        "constraints": "Nonnegative integer smaller than half the active image dimension.",
        "sweep": "Usually fixed; increase it when long/fast trajectories approach image boundaries.",
    },
    "backgrounds.preset": {
        "description": "Named background realism preset from src/backgrounds/presets.py.",
        "dynamic_choices": "background_presets",
        "verified": True,
    },
    "background.preset": {
        "description": "Compatibility copy of the background preset name.",
        "dynamic_choices": "background_presets",
        "verified": True,
    },
    "background.name": {
        "description": "Compatibility/background label; normally match the selected preset.",
        "dynamic_choices": "background_presets",
        "verified": True,
    },
    "temporal_background.mode": {
        "description": "Temporal-background construction mode consumed by the centralized track-level generator.",
        "choices": ["independent", "independent_legacy", "static", "preset_sequence", "advected", "advected_evolving", "correlated_dynamic"],
        "verified": True,
        "note": "Legacy aliases are normalized and strict benchmark fallback policy is enforced.",
    },
    "temporal_background.preset": {
        "description": "Background preset intended for each generated temporal sequence.",
        "dynamic_choices": "background_presets",
        "verified": False,
    },
    "temporal_background.cloud_velocity_yx": {
        "description": "Apparent background/cloud translation per frame in [vertical y, horizontal x] pixels.",
        "constraints": "Two finite numbers [vy, vx].",
        "sweep": "Sweep magnitude and direction when testing robustness to moving clutter.",
    },
    "optics.prf_source": {
        "description": "Chooses generated optics, explicit-profile optics, or an external PRF/Zemax loader branch.",
        "choices": ["generated", "generated_explicit_profiles", "explicit_zernike", "external", "zemax", "codev", "measured"],
        "verified": True,
        "note": "The active prf_setup.py explicitly handles generated_explicit_profiles and preserves profile provenance.",
    },
    "optics.grid_size": {
        "description": "Square pupil/wavefront sampling grid used to synthesize generated PSFs.",
        "constraints": "Positive integer; larger grids improve sampling and increase FFT cost.",
    },
    "optics.field_grid_num_per_axis": {
        "description": "Number of sampled field coordinates along each field-grid axis before radial clipping.",
        "constraints": "Positive integer.",
        "sweep": "Increasing this expands the base PRF library and filter cost.",
    },
    "optics.field_max_radius": {
        "description": "Maximum normalized field radius retained in the generated field grid.",
        "constraints": "Nonnegative number, commonly 1.0.",
    },
    "optics.detector_sigma_pixels": {
        "description": "Detector blur standard deviation applied when converting optical PSF to detector PRF.",
        "constraints": "Nonnegative number in pixels.",
        "sweep": "Useful for detector-sampling mismatch studies.",
    },
    "templates.template_size": {
        "description": "Square side length used to crop/pad each PRF and build matched-filter templates.",
        "constraints": "Positive integer; an odd value centers the template cleanly.",
        "sweep": "Compare sizes large enough to contain aberrated tails without admitting excessive clutter.",
    },
    "templates.centroid_offsets": {
        "description": "Subpixel [y, x] offsets used to expand each base PRF into phase-shifted templates.",
        "constraints": "List of two-number offsets.",
        "sweep": "Common comparisons are 1 phase, 3 one-axis phases, 3×3 phases, and 5×5 phases.",
    },
    "zemax_filter_bank.mode": {
        "description": "Determines whether Zemax PRFs are added to or replace the existing PRF library.",
        "choices": ["append", "replace"],
        "verified": True,
    },
    "zemax_filter_bank.construction_mode": {
        "description": "Selects the Zemax source loader or construction path.",
        "choices": [
            "huygens_psf_manifest", "psf_manifest",
            "zernike_manifest",
            "zernike_wavefront", "zernike_psf", "zernike_synthesis",
            "huygens_psf_csv", "huygens_psf", "psf_csv", "prf_csv",
            "psf", "prf", "npy", "npz", "text_prf", "measured_prf",
            "zernike_txt", "zernike", "zernike_coefficients",
        ],
        "verified": True,
    },
    "zemax_filter_bank.zernike_basis": {
        "description": "Basis label used when interpreting or synthesizing Zernike data.",
        "choices": ["auto", "classic", "fringe", "annular"],
        "verified": False,
        "note": "The supplied synthesizer uses a built-in classic/Noll map; exact Zemax annular normalization is explicitly not guaranteed.",
    },
    "target_program.coordinate_order": {
        "description": "Order used for coordinate pairs.",
        "choices": ["yx"],
        "verified": True,
    },
    "target_program.coordinate_frame": {
        "description": "Coordinate frame used for programmed starts and trajectories.",
        "choices": ["global_image_pixels"],
        "verified": False,
    },
    "target_program.boundary_behavior": {
        "description": "Action requested when a programmed trajectory leaves the valid image/margin region.",
        "choices": ["error"],
        "verified": False,
    },
    "target_program.targets[].shape.type": {
        "description": "Programmed target footprint shape.",
        "choices": ["disk", "ellipse"],
        "verified": False,
        "note": "These values occur in the supplied JSON; no target_program resolver was found in the supplied source dump.",
    },
    "target_program.targets[].trajectory.type": {
        "description": "Programmed motion model.",
        "choices": ["constant_velocity"],
        "verified": False,
    },
    "target_program.targets[].trajectory.velocity_yx_per_frame": {
        "description": "Target velocity in [vertical y, horizontal x] pixels per frame.",
        "constraints": "Two finite numbers.",
        "sweep": "Test slow, medium, and fast motion in several directions.",
    },
    "multi_target.assignment_method": {
        "description": "Method used to assign predicted tracks to target truth for evaluation.",
        "choices": ["greedy", "hungarian"],
        "verified": True,
        "note": "Hungarian uses SciPy linear_sum_assignment when available; the repository includes a greedy fallback.",
    },
    "methods.enabled": {
        "description": "Single-frame detector methods evaluated in the comparative sweep.",
        "choices": [
            "raw", "matched_true", "matched_mismatch_gaussian",
            "filter_bank", "whitened_matched", "signature_verified",
            "deep_detector",
        ],
        "verified": True,
    },
    "detection.response_cfar.method": {
        "description": "CFAR noise estimator.",
        "choices": ["ca", "go", "so", "os"],
        "aliases": [
            "ca: cell_average / cell_averaging",
            "go: greatest_of",
            "so: smallest_of",
            "os: ordered_statistic / ordered",
        ],
        "verified": True,
    },
    "response_cfar.method": {
        "description": "CFAR noise estimator for the compatibility response-CFAR block.",
        "choices": ["ca", "go", "so", "os"],
        "verified": True,
    },
    "detection.response_cfar.score_mode": {
        "description": "Response statistic retained after CFAR normalization.",
        "choices": ["raw", "excess", "cfar_excess", "snr", "cfar_snr", "positive_excess"],
        "verified": True,
    },
    "whitening.psd_mode": {
        "description": "PSD model used by whitened matched filtering.",
        "choices": ["radial", "full_2d"],
        "verified": True,
    },
    "tiling.accept_region": {
        "description": "Region of each overlapping tile allowed to contribute candidates.",
        "choices": ["inner"],
        "verified": False,
    },
    "tiling.matched_filter_backend": {
        "description": "Requested matched-filter execution backend.",
        "choices": ["scipy_cpu", "torch_cuda"],
        "verified": True,
        "note": "The active tiled detector records requested and actual backend/device plus CUDA fallback state.",
    },
    "tiling.cuda_device": {
        "description": "Requested compute device for the tiled matched-filter backend.",
        "choices": ["cpu", "cuda"],
        "verified": True,
    },
    "tracking.classical_response_mode": {
        "description": "Chooses a response from the injected truth template or the maximum across the operational filter bank.",
        "choices": ["truth_template", "bank_max"],
        "verified": True,
        "note": "bank_max is the deployable choice; truth_template is truth-informed and inappropriate for a strict operational benchmark.",
    },
    "tracking.integration_mode": {
        "description": "Temporal integration rule used after velocity alignment.",
        "choices": ["sum", "mean", "trimmed_sum", "persistence"],
        "verified": True,
    },
    "tracking.input_metric": {
        "description": "Controls per-frame target injection calibration.",
        "choices": ["amplitude", "scnr_l2", "scnr_peak"],
        "aliases": [
            "snr / legacy_snr → amplitude",
            "scnr / scnr-l2 → scnr_l2",
            "peak_scnr / scnr-peak → scnr_peak",
        ],
        "verified": True,
    },
    "tracking.adaptive_cfar.calm_method": {
        "description": "CFAR method used in locally calm regions.",
        "choices": ["ca", "go", "so", "os"],
        "verified": True,
    },
    "tracking.adaptive_cfar.edge_method": {
        "description": "CFAR method used near strong spatial edges.",
        "choices": ["ca", "go", "so", "os"],
        "verified": True,
    },
    "tracking.adaptive_cfar.heavy_tail_method": {
        "description": "CFAR method used in heavy-tailed clutter regions.",
        "choices": ["ca", "go", "so", "os"],
        "verified": True,
    },
    "tracking.adaptive_cfar.prescreen_method": {
        "description": "CFAR method used for an optional sparse prescreen.",
        "choices": ["ca", "go", "so", "os"],
        "verified": False,
        "note": "The supplied AdaptiveCFARConfig does not expose this newer field.",
    },
    "tracking.grid_track_before_detect.frame_source": {
        "description": "Frame/map family used by grid track-before-detect.",
        "choices": [
            "classical_event_frames",
            "candidate_detection_frames", "candidate", "candidate_map",
            "whitened", "whitened_frames",
        ],
        "verified": True,
    },
    "tracking.trajectory_volume_reranking.fallback_frame_source": {
        "description": "Requested fallback frame source for trajectory-volume reranking.",
        "choices": ["whitened"],
        "verified": False,
        "note": "The supplied reranker explicitly removes this compatibility field from constructor data.",
    },
    "tracking.tiling.matched_filter_backend": {
        "description": "Nested tracking override for the requested matched-filter backend.",
        "choices": ["scipy_cpu", "torch_cuda"],
        "verified": False,
        "note": "Keep synchronized with top-level tiling.matched_filter_backend.",
    },
    "tracking.tiling.cuda_device": {
        "description": "Nested tracking override for compute device.",
        "choices": ["cpu", "cuda"],
        "verified": False,
    },
    "tracking.candidate_ranking.normalization": {
        "description": "Normalization used before combining candidate-ranking features.",
        "choices": ["frame_robust_z"],
        "verified": False,
        "note": "No candidate_ranking consumer was found in the supplied source dump.",
    },
    "localization_scoring.coordinate_policy.refinement_method": {
        "description": "Subpixel peak-refinement method used for operational coordinates.",
        "choices": ["parabolic_3_point"],
        "verified": False,
        "note": "The source contains parabolic peak refinement, but not this exact configuration key.",
    },
    "operational_event_gate.mode": {
        "description": "Selects development, empirically calibrated, or frozen event-gate behavior.",
        "choices": ["development", "empirical", "frozen"],
        "verified": True,
        "note": "Development mode is calibration-only; freeze and validate independently for final operational claims.",
    },
    "evt.method": {
        "description": "Extreme-value threshold method.",
        "choices": ["empirical", "gpd"],
        "verified": True,
    },
    "track_level_evt.method": {
        "description": "Track-level threshold estimation method.",
        "choices": ["gpd", "empirical", "quantile", "direct"],
        "verified": True,
        "note": "empirical, quantile, and direct enter the empirical branch.",
    },
    "ml.model_name": {
        "description": "Neural detector architecture alias.",
        "choices": ["small_cnn", "small_cnn_heatmap", "small", "unet", "unet_heatmap", "small_unet"],
        "verified": True,
    },
    "ml.device": {
        "description": "Device used by the optional learned detector.",
        "choices": ["cpu", "cuda", "mps"],
        "verified": True,
    },
    "ml.input_normalization": {
        "description": "Requested input normalization for the learned model.",
        "choices": ["robust_zscore"],
        "verified": False,
        "note": "The supplied inference implementation applies robust z-scoring directly and does not read this exact field.",
    },
    "config_compatibility.canonical_frame_order": {
        "description": "Documented canonical external sequence order: time, height, width.",
        "choices": ["THW"],
        "verified": False,
    },
    "config_compatibility.legacy_scalar_size_policy": {
        "description": "Documented rule for reducing rectangular dimensions to a legacy scalar size.",
        "choices": ["max"],
        "verified": False,
    },
}


SWEEP_GUIDANCE = {
    "scene": (
        "Geometry sweeps should compare image scale and target margin separately. The standard synthetic "
        "runner is square-only in the supplied source; rectangular H×W studies require a cached/external "
        "adapter and validation that every consumer uses actual array.shape."
    ),
    "backgrounds": (
        "Sweep named presets as discrete cases. Keep random seeds paired when comparing methods so each "
        "method sees the same realization."
    ),
    "optics": (
        "Compare bank composition, field position, detector blur, focus/wavelength groups, and source "
        "family. Validate external/Zemax paths independently before mixing them with generated PRFs."
    ),
    "templates": (
        "Sweep template_size and centroid_offsets separately. Report filter count and runtime because "
        "centroid-phase count multiplies the bank directly."
    ),
    "detection": (
        "Tune preprocessing and candidate generation using truth-candidate survival, false candidate count, "
        "localization error, and runtime—not candidate count alone."
    ),
    "tiling": (
        "Sweep tile_size, overlap, per-tile cap, and per-frame cap. Check duplicate-cluster metadata and "
        "truth retention at tile boundaries."
    ),
    "tracking": (
        "After candidate generation is stable, sweep association/gate radius, support fraction, longest "
        "streak, allowed misses, speed range, and pruning caps."
    ),
    "operational_event_gate": (
        "Calibrate hard gates first, then threshold/PFA. Low-PFA claims require enough independent noise "
        "sequences; direct empirical resolution is 1/N_noise."
    ),
    "track_level_evt": (
        "Sweep target PFA only with adequate noise samples. Treat GPD results as model-based extrapolation "
        "and validate them on an independent noise set."
    ),
}


def discover_background_presets(repo_root: Path) -> list[str]:
    """Read preset names from the installed repository without importing SciPy."""

    path = repo_root / "src" / "backgrounds" / "presets.py"
    if not path.exists():
        return list(BACKGROUND_PRESET_FALLBACK)

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        start = source.find("BACKGROUND_PRESETS")
        end = source.find("def list_background_presets", start)
        block = source[start:end if end >= 0 else None]
        names = sorted(set(re.findall(r'(?m)^\s{4}"([^"]+)"\s*:', block)))
        return names or list(BACKGROUND_PRESET_FALLBACK)
    except Exception:
        return list(BACKGROUND_PRESET_FALLBACK)


def load_repository_source_index(repo_root: Path) -> dict[str, str]:
    """Load small Python sources once for help/evidence reporting."""

    out: dict[str, str] = {}
    candidates = []
    for folder in (repo_root / "src", repo_root / "experiments"):
        if folder.exists():
            candidates.extend(folder.rglob("*.py"))
    candidates.extend(repo_root.glob("*.py"))

    for path in sorted(set(candidates)):
        try:
            if path.stat().st_size > 2_000_000:
                continue
            out[str(path.relative_to(repo_root))] = path.read_text(
                encoding="utf-8", errors="replace"
            )
        except Exception:
            continue
    return out


def repository_key_usage(source_index: dict[str, str], key: str) -> list[str]:
    """Return files that directly contain a quoted JSON key."""

    q = re.compile(r'["\']' + re.escape(str(key)) + r'["\']')
    return [name for name, source in source_index.items() if q.search(source)]


def _json_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _unique_values(values: list[Any]) -> list[Any]:
    seen = set()
    out = []
    for value in values:
        try:
            marker = json.dumps(value, sort_keys=True, ensure_ascii=False)
        except Exception:
            marker = repr(value)
        if marker not in seen:
            seen.add(marker)
            out.append(value)
    return out


def flatten_section_schema(section_key: str, value: Any) -> list[dict[str, Any]]:
    """Flatten every leaf field, merging schemas across list items."""

    collected: dict[str, list[Any]] = {}
    order: list[str] = []

    def add(path: str, item: Any) -> None:
        if path not in collected:
            collected[path] = []
            order.append(path)
        collected[path].append(item)

    def visit(item: Any, path: str) -> None:
        if isinstance(item, dict):
            if not item:
                add(path, item)
                return
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else str(key)
                visit(child, child_path)
            return

        if isinstance(item, list):
            if item and any(isinstance(child, dict) for child in item):
                for child in item:
                    if isinstance(child, dict):
                        visit(child, path + "[]")
                    else:
                        add(path, item)
                return
            add(path, item)
            return

        add(path, item)

    visit(value, section_key)
    return [
        {
            "path": path,
            "values": _unique_values(collected[path]),
        }
        for path in order
    ]


def _leaf_name(path: str) -> str:
    return path.rsplit(".", 1)[-1].replace("[]", "")


def _smart_field_description(path: str, value: Any) -> str:
    meta = FIELD_METADATA.get(path)
    if meta and meta.get("description"):
        return str(meta["description"])

    leaf = _leaf_name(path)
    readable = leaf.replace("_", " ")
    parent = path.rsplit(".", 1)[0] if "." in path else ""

    if isinstance(value, bool) or leaf == "enabled":
        return f"Boolean switch controlling whether {parent or readable} is active."
    if leaf.endswith("_weight") or leaf.startswith("weight_"):
        term = readable.replace(" weight", "").replace("weight ", "")
        return (
            f"Relative coefficient for the {term} term. Increasing a positive weight gives that term "
            "more influence; fields explicitly described as penalties usually subtract influence."
        )
    if leaf.startswith("min_"):
        return f"Minimum accepted {readable[4:]}; values below it fail the owning gate or are not retained."
    if leaf.startswith("max_"):
        return f"Maximum allowed or retained {readable[4:]}; values above it are rejected, clipped, or capped."
    if leaf.startswith("num_"):
        return f"Count of {readable[4:]} used or expected by this section."
    if leaf.endswith("_fraction"):
        return f"Fractional {readable[:-9]} setting, normally between 0 and 1."
    if leaf.endswith("_percentile"):
        return f"Percentile used for {readable[:-11]}, normally between 0 and 100."
    if leaf.endswith("_pixels"):
        return f"{readable.capitalize()} measured in image pixels."
    if leaf.endswith("_per_frame"):
        return f"{readable.capitalize()} expressed per temporal frame."
    if leaf.endswith("_sigma") or "sigma_" in leaf:
        return f"Standard-deviation or Gaussian-scale parameter for {readable}."
    if leaf.endswith("_key"):
        return (
            f"Name of the dictionary field, response map, or table column used as {readable[:-4]}. "
            "This is generally open-ended but must match a key actually produced upstream."
        )
    if leaf.endswith("_path") or leaf.endswith("_file") or leaf.endswith("_filename"):
        return f"File-system location for {readable}; relative paths normally resolve from the repository root."
    if leaf.endswith("_root"):
        return f"Parent directory used to resolve files associated with {readable}."
    if leaf.endswith("_mode"):
        return f"Selects the {readable} implementation or operating behavior."
    if leaf.endswith("_method"):
        return f"Selects the algorithm used for {readable[:-7]}."
    if leaf.endswith("_backend"):
        return f"Selects the numerical implementation used for {readable[:-8]}."
    if leaf.endswith("_device"):
        return f"Selects the compute device used for {readable[:-7]}."
    if leaf.endswith("_note") or leaf in {"note", "notes", "purpose", "reason"}:
        return "Explanatory metadata; it normally documents intent and does not alter numerical execution."
    if leaf.startswith("save_") or leaf.startswith("export_") or leaf.startswith("include_"):
        return f"Controls whether {readable.split(' ', 1)[1] if ' ' in readable else readable} is retained or reported."
    if leaf.startswith("allow_"):
        return f"Allows the stated behavior: {readable[6:]}."
    if leaf.startswith("require_"):
        return f"Requires the stated condition: {readable[8:]}."
    return f"Controls or documents the {readable} setting within {parent or 'the configuration'}."


def _choices_for_field(
    path: str,
    value: Any,
    background_presets: list[str],
) -> tuple[list[Any], str, list[str], str]:
    meta = FIELD_METADATA.get(path, {})
    choices = list(meta.get("choices", []))
    if meta.get("dynamic_choices") == "background_presets":
        choices = list(background_presets)

    if isinstance(value, bool) and not choices:
        choices = [True, False]
        status = "JSON type"
    else:
        verified = meta.get("verified", None)
        status = (
            "source-backed"
            if verified is True
            else "configuration convention; verify installed code"
            if verified is False
            else ""
        )

    aliases = list(meta.get("aliases", []))
    note = str(meta.get("note", ""))
    return choices, status, aliases, note


def _constraints_for_field(path: str, value: Any) -> str:
    meta = FIELD_METADATA.get(path, {})
    if meta.get("constraints"):
        return str(meta["constraints"])

    leaf = _leaf_name(path)
    if isinstance(value, bool):
        return "Boolean: true or false."
    if leaf.endswith("_fraction"):
        return "Usually a number in [0, 1]."
    if leaf.endswith("_percentile"):
        return "Usually a number in [0, 100]."
    if leaf.startswith(("num_", "max_", "min_")) and isinstance(value, int):
        return "Integer; use a value consistent with sequence length and available resources."
    return ""


def build_repository_profile(
    section_key: str,
    editor_text: str,
    *,
    background_presets: list[str],
    source_index: dict[str, str],
) -> dict[str, Any]:
    try:
        parsed = json.loads(editor_text)
    except Exception:
        parsed = {}

    value = parsed.get(section_key) if isinstance(parsed, dict) else None
    canonical = ALIASES.get(section_key, section_key)
    summary = REFINED_SECTION_SUMMARIES.get(
        section_key,
        REFINED_SECTION_SUMMARIES.get(
            canonical,
            f"Controls the {section_key.replace('_', ' ')} portion of the experiment.",
        ),
    )

    fields = []
    for row in flatten_section_schema(section_key, value):
        path = row["path"]
        values = row["values"]
        representative = values[0] if len(values) == 1 else values
        choices, choice_status, aliases, note = _choices_for_field(
            path, representative, background_presets
        )
        fields.append({
            "path": path,
            "description": _smart_field_description(path, representative),
            "values": values,
            "type": _json_type_name(representative),
            "choices": choices,
            "choice_status": choice_status,
            "aliases": aliases,
            "constraints": _constraints_for_field(path, representative),
            "note": note,
        })

    # Source-wide quoted-key searching is deliberately not performed here.
    # Scanning every repository file during startup or on each click made the
    # editor unresponsive on large Windows/OneDrive checkouts.
    if section_key in REFINED_SECTION_SUMMARIES:
        source_status = (
            "This profile is included in the repository-grounded documentation "
            "embedded in the builder. The builder does not rescan the entire "
            "repository on each click."
        )
    else:
        source_status = (
            "This section was read from the loaded JSON. Its field descriptions "
            "are generated from the current values and known OPIR conventions."
        )

    return {
        "title": section_key.replace("_", " ").title(),
        "summary": summary,
        "fields": fields,
        "single": value,
        "sweep_text": SWEEP_GUIDANCE.get(
            section_key,
            "Keep unrelated settings fixed, vary one hypothesis-driven group at a time, use paired seeds, "
            "and record the resolved configuration with every case.",
        ),
        "source_status": source_status,
    }


ALIASES = {
    "background": "temporal_background",
    "backgrounds": "temporal_background",
    "cfar": "candidate_generation",
    "candidate": "candidate_generation",
    "event_gate": "operational_event_gate",
    "visualization": "diagnostics",
    "debug_visualization": "diagnostics",
}


def cpu_single() -> dict[str, Any]:
    return {
        "description": "Single CPU OPIR validation run",
        "config_revision": "cpu_single_v1",
        "random_seed": 12345,
        "output": {"root": "outputs/mtv", "run_name": "opir_cpu_single", "handoff_subfolder": "handoff_outputs"},
        "output_policy": {
            "enabled": True,
            "mode": "single_comprehensive_file",
            "filename": "comparative_detection_tracking_metrics.json.gz",
            "compression_level": 6,
            "cleanup_intermediate_outputs": True,
            "retain_intermediates_on_failure": True,
        },
        "optics": {
            "prf_source": "generated_explicit_profiles",
            "explicit_zernike_profiles_file": "configs/aberrations/explicit_zernike_profile_bank_13.json",
            "explicit_profile_selection": ["center", "left_edge", "right_edge"],
        },
        "templates": {
            "template_size": 33,
            "centroid_offsets_yx": [[-0.5, 0.0], [0.0, 0.0], [0.5, 0.0]],
            "zero_mean": True,
            "unit_norm": True,
        },
        "scnr_l2_values": [2.75],
        "temporal_background": {"preset": "satellite_visual_cloud_front_soft", "dynamic": True},
        "tiling": {
            "enabled": True,
            "tile_size": 256,
            "overlap": 32,
            "matched_filter_backend": "scipy_cpu",
            "cuda_device": "cpu",
            "cuda_filter_batch_size": 1,
            "max_candidates_per_tile": 50,
            "max_candidates_per_frame": 100,
        },
        "candidate_generation": {"method": "positive_local_z", "local_z_threshold": 3.0},
        "tracking": {
            "num_frames": 8,
            "workers": 8,
            "minimum_support_frames": 3,
            "minimum_support_fraction": 0.375,
            "maximum_consecutive_misses": 1,
            "association_radius_pixels": 6.0,
            "tiling": {
                "enabled": True,
                "tile_size": 256,
                "overlap": 32,
                "matched_filter_backend": "scipy_cpu",
                "cuda_device": "cpu",
                "cuda_filter_batch_size": 1,
                "max_candidates_per_tile": 50,
                "max_candidates_per_frame": 100,
            },
        },
        "operational_event_gate": {"enabled": True, "mode": "empirical", "requested_pfa": 0.05},
        "reporting": {
            "enabled": True,
            "primary_track_scores": ["operational_event_score", "localization_rank_score"],
            "primary_pfa_for_ranking": 0.05,
            "hide_empty_scores": True,
            "figures": {
                "enabled": False,
                "include_sweep_figures": False,
                "include_standard_score_figures": False,
                "include_multitarget_figures": False,
                "include_zemax_comparison": False,
            },
        },
        "profiling": {
            "enabled": True,
            "save_stage_timings": True,
            "save_candidate_cap_summary": True,
            "save_truth_retention_summary": True,
            "save_cuda_timing_summary": False,
        },
        "diagnostics": {
            "export_candidate_rank_diagnostics": True,
            "candidate_rank_export_top_k": 30,
            "export_debug_visualization": False,
            "max_debug_sequences": 0,
        },
        "sweep": {"enabled": False, "mode": "cases", "cases": []},
    }


def cpu_sweep() -> dict[str, Any]:
    value = cpu_single()
    value["description"] = "CPU SCNR and background sweep"
    value["config_revision"] = "cpu_sweep_v1"
    value["output"]["run_name"] = "opir_cpu_scnr_background_sweep"
    value["output_policy"] = {
        "enabled": False,
        "mode": "development_full",
        "cleanup_intermediate_outputs": False,
        "retain_intermediates_on_failure": True,
    }
    value["reporting"]["figures"] = {
        "enabled": True,
        "include_sweep_figures": True,
        "include_standard_score_figures": True,
        "include_multitarget_figures": True,
        "include_zemax_comparison": False,
    }
    value["sweep"] = HELP["sweep"]["sweep"]
    return value


def gpu_single() -> dict[str, Any]:
    value = cpu_single()
    value["description"] = "Single GPU OPIR validation run"
    value["config_revision"] = "gpu_single_v1"
    value["output"]["run_name"] = "opir_gpu_single"
    for block in (value["tiling"], value["tracking"]["tiling"]):
        block["matched_filter_backend"] = "torch_cuda"
        block["cuda_device"] = "cuda"
        block["cuda_filter_batch_size"] = 4
    value["profiling"]["save_cuda_timing_summary"] = True
    return value


def hdf5_prf() -> dict[str, Any]:
    value = cpu_single()
    value["description"] = "CPU run using HDF5 PRF files"
    value["config_revision"] = "cpu_hdf5_prf_v1"
    value["output"]["run_name"] = "opir_cpu_hdf5_prf"
    value["optics"] = {
        "prf_source": "external",
        "external_prfs": [
            {
                "name": "center",
                "path": "datasets/optical_prfs/center_prf.h5",
                "hdf5_dataset": "prf",
                "field_x": 0.0,
                "field_y": 0.0,
                "source_family": "hdf5_prf",
            },
            {
                "name": "left_edge",
                "path": "datasets/optical_prfs/left_edge_prf.h5",
                "hdf5_dataset": "prf",
                "field_x": -1.0,
                "field_y": 0.0,
                "source_family": "hdf5_prf",
            },
            {
                "name": "right_edge",
                "path": "datasets/optical_prfs/right_edge_prf.h5",
                "hdf5_dataset": "prf",
                "field_x": 1.0,
                "field_y": 0.0,
                "source_family": "hdf5_prf",
            },
        ],
    }
    return value


def full_outputs() -> dict[str, Any]:
    value = cpu_single()
    value["description"] = "CPU run retaining full analytical outputs"
    value["config_revision"] = "cpu_full_outputs_v1"
    value["output"]["run_name"] = "opir_cpu_full_outputs"
    value["output_policy"] = {
        "enabled": False,
        "mode": "development_full",
        "cleanup_intermediate_outputs": False,
        "retain_intermediates_on_failure": True,
    }
    value["reporting"]["figures"] = {
        "enabled": True,
        "include_sweep_figures": True,
        "include_standard_score_figures": True,
        "include_multitarget_figures": True,
        "include_zemax_comparison": False,
        "scene_examples": {
            "enabled": True,
            "max_sequences": 2,
            "show_all_targets": True,
            "show_target_ids": True,
            "show_selected_detections": True,
            "show_assigned_tracks": True,
            "show_candidate_peaks": True,
            "candidate_top_k": 20,
        },
    }
    return value


TEMPLATES = {
    "CPU single run": cpu_single,
    "CPU SCNR/background sweep": cpu_sweep,
    "GPU single run": gpu_single,
    "CPU HDF5 PRF run": hdf5_prf,
    "CPU full analytical outputs": full_outputs,
}


def pretty(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def section_at_line(text: str, line_number: int) -> str:
    lines = text.splitlines()
    line_number = max(1, min(line_number, max(1, len(lines))))
    pattern = re.compile(r'^(\s*)"([^"]+)"\s*:')
    stack: list[tuple[int, str]] = []
    for index, line in enumerate(lines, 1):
        match = pattern.match(line)
        if match:
            indent = len(match.group(1).replace("\t", "  "))
            key = match.group(2)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, key))
        if index >= line_number:
            break
    for _indent, key in reversed(stack):
        key = ALIASES.get(key, key)
        if key in HELP:
            return key
    return "root"


def top_level_section_ranges(text: str) -> list[tuple[str, int, int]]:
    """Return top-level JSON section ranges as (key, start_line, end_line)."""

    lines = text.splitlines()
    starts: list[tuple[str, int]] = []
    pattern = re.compile(r'^  "([^"]+)"\s*:')

    for line_number, line in enumerate(lines, start=1):
        match = pattern.match(line)
        if match:
            starts.append((match.group(1), line_number))

    ranges: list[tuple[str, int, int]] = []
    for index, (key, start_line) in enumerate(starts):
        if index + 1 < len(starts):
            end_line = starts[index + 1][1] - 1
        else:
            end_line = len(lines)
            while end_line > start_line and not lines[end_line - 1].strip():
                end_line -= 1
            if end_line > start_line and lines[end_line - 1].strip() == "}":
                end_line -= 1
        ranges.append((key, start_line, max(start_line, end_line)))

    return ranges


def section_range_at_line(text: str, line_number: int) -> tuple[str, int, int] | None:
    for key, start_line, end_line in top_level_section_ranges(text):
        if start_line <= line_number <= end_line:
            return key, start_line, end_line
    return None


class Editor(ttk.Frame):
    def __init__(self, master: tk.Widget, callback) -> None:
        super().__init__(master)
        self.callback = callback
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.numbers = tk.Canvas(self, width=52, background="#f3f4f6", highlightthickness=0)
        self.text = tk.Text(
            self,
            wrap="none",
            undo=True,
            maxundo=-1,
            font=("Consolas", 11),
            padx=8,
            pady=8,
            background="#ffffff",
            foreground="#1f2937",
            insertbackground="#111827",
            selectbackground="#bfdbfe",
        )
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=self._yscroll, xscrollcommand=self.hbar.set)

        self.numbers.grid(row=0, column=0, sticky="ns")
        self.text.grid(row=0, column=1, sticky="nsew")
        self.vbar.grid(row=0, column=2, sticky="ns")
        self.hbar.grid(row=1, column=1, sticky="ew")

        self.text.tag_configure("key", foreground="#005cc5")
        self.text.tag_configure("string", foreground="#22863a")
        self.text.tag_configure("number", foreground="#b31d28")
        self.text.tag_configure("literal", foreground="#6f42c1")
        self.text.tag_configure("error", background="#fee2e2")
        self.text.tag_configure("section_hover", background="#eef6ff")
        self.text.tag_configure("section_selected", background="#dbeafe")

        self.job = None
        self.hover_callback = None
        self.click_callback = None
        self.text.bind("<<Modified>>", self._modified)
        self.text.bind("<KeyRelease>", lambda _e: self.callback())
        self.text.bind("<ButtonRelease-1>", lambda _e: self.callback())
        self.text.bind("<Configure>", lambda _e: self.after_idle(self.redraw))
        self.text.bind("<MouseWheel>", lambda _e: self.after_idle(self.redraw))
        self.text.bind("<Motion>", self._motion)
        self.text.bind("<Leave>", self._leave)
        self.text.bind("<Button-1>", self._click, add="+")

    def _yview(self, *args) -> None:
        self.text.yview(*args)
        self.redraw()

    def _yscroll(self, first, last) -> None:
        self.vbar.set(first, last)
        self.redraw()

    def _modified(self, _event) -> None:
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.callback()
            self.schedule_highlight()
            self.after_idle(self.redraw)

    def _motion(self, event) -> None:
        if self.hover_callback is None:
            return
        index = self.text.index(f"@{event.x},{event.y}")
        line = int(index.split(".")[0])
        self.hover_callback(line)

    def _leave(self, _event) -> None:
        self.text.tag_remove("section_hover", "1.0", "end")

    def _click(self, event) -> None:
        if self.click_callback is None:
            return
        index = self.text.index(f"@{event.x},{event.y}")
        line = int(index.split(".")[0])
        self.after_idle(self.click_callback, line)

    def highlight_hover_section(self, start_line: int | None, end_line: int | None) -> None:
        self.text.tag_remove("section_hover", "1.0", "end")
        if start_line is None or end_line is None:
            return
        self.text.tag_add("section_hover", f"{start_line}.0", f"{end_line}.end+1c")
        self.text.tag_lower("section_hover")
        self.text.tag_raise("section_selected")

    def highlight_selected_section(self, start_line: int | None, end_line: int | None) -> None:
        self.text.tag_remove("section_selected", "1.0", "end")
        if start_line is None or end_line is None:
            return
        self.text.tag_add("section_selected", f"{start_line}.0", f"{end_line}.end+1c")
        self.text.tag_lower("section_selected")
        self.text.tag_raise("error")

    def get(self) -> str:
        return self.text.get("1.0", "end-1c")

    def set(self, value: str) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.edit_modified(False)
        self.schedule_highlight()
        self.after_idle(self.redraw)

    def line(self) -> int:
        return int(self.text.index("insert").split(".")[0])

    def redraw(self) -> None:
        self.numbers.delete("all")
        index = self.text.index("@0,0")
        while True:
            info = self.text.dlineinfo(index)
            if info is None:
                break
            self.numbers.create_text(
                46, info[1], anchor="ne", text=index.split(".")[0],
                fill="#667085", font=("Consolas", 10)
            )
            index = self.text.index(f"{index}+1line")

    def schedule_highlight(self) -> None:
        if self.job:
            self.after_cancel(self.job)
        self.job = self.after(220, self.highlight)

    def highlight(self) -> None:
        self.job = None
        value = self.get()
        for tag in ("key", "string", "number", "literal"):
            self.text.tag_remove(tag, "1.0", "end")
        rules = [
            ("key", re.compile(r'"(?:\\.|[^"\\])*"(?=\s*:)')),
            ("string", re.compile(r'"(?:\\.|[^"\\])*"(?!\s*:)')),
            ("number", re.compile(r'(?<![\w"])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')),
            ("literal", re.compile(r'\b(?:true|false|null)\b')),
        ]
        for tag, pattern in rules:
            for match in pattern.finditer(value):
                self.text.tag_add(tag, f"1.0+{match.start()}c", f"1.0+{match.end()}c")

    def error_line(self, line: int) -> None:
        self.text.tag_remove("error", "1.0", "end")
        self.text.tag_add("error", f"{line}.0", f"{line}.end")
        self.text.mark_set("insert", f"{line}.0")
        self.text.see(f"{line}.0")
        self.text.focus_set()


class App(tk.Tk):
    def __init__(self, initial: Path | None = None) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1540x930")
        self.minsize(1060, 680)
        self.repo = Path.cwd()
        self.current: Path | None = None
        self.dirty = False
        self.process: subprocess.Popen[str] | None = None
        self._editor_update_job: str | None = None
        self._current_help_key = ""
        self._loading_editor = False
        self._selected_section_key = ""
        self._config_files: list[Path] = []
        # Keep startup immediate. The previous version synchronously read every
        # Python source file before Tkinter entered its event loop, which could
        # make the program appear frozen until Ctrl+C interrupted the scan.
        self.background_presets = list(BACKGROUND_PRESET_FALLBACK)
        self.repository_sources: dict[str, str] = {}
        self._repository_index_ready = False
        self._repository_index_started = False

        self._menu()
        self._layout()
        self.protocol("WM_DELETE_WINDOW", self.close)

        # Load only the small background-preset registry after the window is
        # already responsive. Full source indexing is intentionally disabled.
        self.after(150, self._load_small_repository_metadata)

        if initial:
            self.load_file(initial, confirm=False)
        else:
            configured = Path(DEFAULT_TEMPLATE_JSON) if DEFAULT_TEMPLATE_JSON else None
            if configured is not None and not configured.is_absolute():
                configured = self.repo / configured
            if configured is not None and configured.exists():
                self.load_file(configured, confirm=False)
            else:
                self.load_template("CPU single run", confirm=False)

    def _load_small_repository_metadata(self) -> None:
        """Load small optional metadata without blocking initial window creation."""
        try:
            self.background_presets = discover_background_presets(self.repo)
        except Exception:
            self.background_presets = list(BACKGROUND_PRESET_FALLBACK)

        # Refresh the currently displayed profile only when the user is idle.
        if self._current_help_key:
            self.after_idle(
                lambda: self.show_help(self._current_help_key, force=True)
            )

    def _menu(self) -> None:
        menu = tk.Menu(self)
        filem = tk.Menu(menu, tearoff=False)
        filem.add_command(label="Open...", command=self.open_file)
        filem.add_command(label="Save", command=self.save)
        filem.add_command(label="Save as...", command=self.save_as)
        filem.add_separator()
        filem.add_command(label="Exit", command=self.close)
        menu.add_cascade(label="File", menu=filem)

        editm = tk.Menu(menu, tearoff=False)
        editm.add_command(label="Validate JSON", command=self.validate)
        editm.add_command(label="Format JSON", command=self.format_json)
        menu.add_cascade(label="Edit", menu=editm)

        runm = tk.Menu(menu, tearoff=False)
        runm.add_command(label="Run experiment", command=self.run)
        runm.add_command(label="Stop experiment", command=self.stop)
        menu.add_cascade(label="Run", menu=runm)
        self.config(menu=menu)

        self.bind_all("<Control-o>", lambda _e: self.open_file())
        self.bind_all("<Control-s>", lambda _e: self.save())
        self.bind_all("<Control-Shift-S>", lambda _e: self.save_as())
        self.bind_all("<Control-Return>", lambda _e: self.validate())

    def _layout(self) -> None:
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        topbar = ttk.Frame(self, padding=(6, 6, 6, 2))
        topbar.grid(row=0, column=0, sticky="ew")
        topbar.grid_columnconfigure(1, weight=1)

        ttk.Label(topbar, text="Existing experiment JSON:").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.config_picker_var = tk.StringVar()
        self.config_picker = ttk.Combobox(
            topbar,
            textvariable=self.config_picker_var,
            state="normal",
            width=70,
        )
        self.config_picker.grid(row=0, column=1, sticky="ew")
        self.config_picker.bind("<<ComboboxSelected>>", lambda _e: self.load_selected_config())
        self.config_picker.bind("<Return>", lambda _e: self.load_selected_config())
        self.config_picker.bind("<KeyRelease>", self.filter_config_picker)

        ttk.Button(topbar, text="Load", command=self.load_selected_config).grid(row=0, column=2, padx=4)
        ttk.Button(topbar, text="Refresh", command=self.refresh_config_files).grid(row=0, column=3, padx=2)

        toolbar = ttk.Frame(self, padding=(6, 2, 6, 6))
        toolbar.grid(row=1, column=0, sticky="ew")
        for label, command in (
            ("Open", self.open_file),
            ("Save", self.save),
            ("Save As", self.save_as),
            ("Validate", self.validate),
            ("Format", self.format_json),
            ("Run Experiment", self.run),
            ("Stop", self.stop),
        ):
            ttk.Button(toolbar, text=label, command=command).pack(side="left", padx=2)

        ttk.Label(toolbar, text="Template:").pack(side="left", padx=(18, 4))
        self.template_var = tk.StringVar(value="CPU single run")
        combo = ttk.Combobox(toolbar, textvariable=self.template_var, values=list(TEMPLATES), state="readonly", width=31)
        combo.pack(side="left")
        ttk.Button(toolbar, text="Load Template", command=lambda: self.load_template(self.template_var.get())).pack(side="left", padx=3)

        self.file_var = tk.StringVar(value="Unsaved configuration")
        ttk.Label(toolbar, textvariable=self.file_var, foreground="#667085").pack(side="right", padx=8)

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")
        left = ttk.Frame(panes, padding=(6, 0, 3, 6))
        right = ttk.Frame(panes, padding=(3, 0, 6, 6))
        panes.add(left, weight=3)
        panes.add(right, weight=2)

        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        header = ttk.Frame(left)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(header, text="JSON configuration", font=("Segoe UI", 13, "bold")).pack(side="left")
        self.line_var = tk.StringVar(value="Line 1")
        ttk.Label(header, textvariable=self.line_var, foreground="#667085").pack(side="right")
        self.editor = Editor(left, self.changed)
        self.editor.hover_callback = self.hover_section
        self.editor.click_callback = self.click_section
        self.editor.grid(row=1, column=0, sticky="nsew")

        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        ttk.Label(right, text="Configuration guidance", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))

        hpane = ttk.Panedwindow(right, orient="vertical")
        hpane.grid(row=1, column=0, sticky="nsew")
        nav = ttk.Frame(hpane)
        detail = ttk.Frame(hpane)
        hpane.add(nav, weight=1)
        hpane.add(detail, weight=4)

        nav.grid_rowconfigure(1, weight=1)
        nav.grid_columnconfigure(0, weight=1)
        searchrow = ttk.Frame(nav)
        searchrow.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        searchrow.grid_columnconfigure(1, weight=1)
        ttk.Label(searchrow, text="Search help:").grid(row=0, column=0, padx=(0, 4))
        self.search_var = tk.StringVar()
        ttk.Entry(searchrow, textvariable=self.search_var).grid(row=0, column=1, sticky="ew")
        self.search_var.trace_add("write", lambda *_: self.refresh_tree())

        self.tree = ttk.Treeview(nav, show="tree", height=8)
        self.tree.grid(row=1, column=0, sticky="nsew")
        tbar = ttk.Scrollbar(nav, orient="vertical", command=self.tree.yview)
        tbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tbar.set)
        self.tree.bind("<<TreeviewSelect>>", self.tree_select)

        detail.grid_rowconfigure(0, weight=1)
        detail.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(detail, background="#f8fafc", highlightthickness=0)
        cbar = ttk.Scrollbar(detail, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=cbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        cbar.grid(row=0, column=1, sticky="ns")
        self.inner = ttk.Frame(self.canvas, padding=10)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))

        # The active lower-right field viewer is recreated whenever a JSON
        # section is selected. Keep a reference so application-level trackpad
        # events can be routed to the correct scrollable widget.
        self.profile_field_text: tk.Text | None = None
        self.bind_all("<MouseWheel>", self._route_trackpad_scroll, add="+")
        self.bind_all("<Button-4>", self._route_trackpad_scroll, add="+")
        self.bind_all("<Button-5>", self._route_trackpad_scroll, add="+")

        self.status_var = tk.StringVar(
            value="Ready — repository-wide source scanning is disabled for responsive startup"
        )
        ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 4)).grid(row=3, column=0, sticky="ew")

        self.refresh_tree()
        self.refresh_config_files()
        self.show_help("root", force=True)

    def refresh_config_files(self) -> None:
        folder = self.repo / CONFIG_DIR
        if not folder.exists():
            self._config_files = []
            self.config_picker.configure(values=[])
            self.status_var.set(f"Configuration folder not found: {folder}")
            return

        self._config_files = sorted(
            folder.glob("*.json"),
            key=lambda path: path.name.lower(),
        )
        names = [path.name for path in self._config_files]
        self.config_picker.configure(values=names)
        if self.current is not None and self.current.parent == folder:
            self.config_picker_var.set(self.current.name)
        elif names and not self.config_picker_var.get().strip():
            self.config_picker_var.set(names[0])
        self.status_var.set(f"Found {len(names)} experiment JSON files")

    def filter_config_picker(self, event) -> None:
        if event.keysym in {"Return", "Up", "Down", "Escape", "Tab"}:
            return
        query = self.config_picker_var.get().strip().lower()
        values = [
            path.name
            for path in self._config_files
            if query in path.name.lower()
        ]
        self.config_picker.configure(values=values)

    def load_selected_config(self) -> None:
        value = self.config_picker_var.get().strip()
        if not value:
            return

        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.repo / CONFIG_DIR / candidate

        if not candidate.exists():
            matches = [
                path for path in self._config_files
                if value.lower() in path.name.lower()
            ]
            if len(matches) == 1:
                candidate = matches[0]
            elif len(matches) > 1:
                self.config_picker.configure(values=[path.name for path in matches])
                self.config_picker.event_generate("<Button-1>")
                self.status_var.set("Multiple matching JSON files found; choose one")
                return
            else:
                messagebox.showerror("Configuration not found", str(candidate))
                return

        self.load_file(candidate)

    def hover_section(self, line_number: int) -> None:
        result = section_range_at_line(self.editor.get(), line_number)
        if result is None:
            self.editor.highlight_hover_section(None, None)
            return
        _key, start_line, end_line = result
        self.editor.highlight_hover_section(start_line, end_line)

    def click_section(self, line_number: int) -> None:
        result = section_range_at_line(self.editor.get(), line_number)
        if result is None:
            return
        key, start_line, end_line = result
        self._selected_section_key = key
        self.editor.highlight_selected_section(start_line, end_line)
        self.show_help(ALIASES.get(key, key), force=True)

    def refresh_tree(self) -> None:
        query = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())

        keys = []
        try:
            loaded = json.loads(self.editor.get())
            if isinstance(loaded, dict):
                keys.extend(str(key) for key in loaded.keys())
        except Exception:
            pass

        for key in HELP:
            if key not in keys:
                keys.append(key)

        for key in keys:
            if key in REFINED_SECTION_SUMMARIES:
                title = key.replace("_", " ").title()
                summary = REFINED_SECTION_SUMMARIES[key]
                field_names = ""
                value = _section_value_from_editor(self.editor.get(), key)
                if isinstance(value, dict):
                    field_names = " ".join(str(item) for item in value.keys())
            else:
                item = HELP.get(key, HELP["root"])
                title = item["title"]
                summary = item["summary"]
                field_names = " ".join(item["fields"])

            haystack = f"{key} {title} {summary} {field_names}".lower()
            if query and query not in haystack:
                continue
            self.tree.insert("", "end", iid=key, text=title)

    def tree_select(self, _event) -> None:
        selection = self.tree.selection()
        if selection:
            self.show_help(selection[0], force=True)

    def clear_help(self) -> None:
        self.profile_field_text = None
        for child in self.inner.winfo_children():
            child.destroy()

    @staticmethod
    def _widget_is_descendant(widget: tk.Widget | None, ancestor: tk.Widget | None) -> bool:
        if widget is None or ancestor is None:
            return False
        current = widget
        while current is not None:
            if current == ancestor:
                return True
            parent_name = current.winfo_parent()
            if not parent_name:
                break
            try:
                current = current._nametowidget(parent_name)
            except Exception:
                break
        return False

    @staticmethod
    def _wheel_units(event) -> int:
        # Windows precision touchpads may emit deltas smaller than 120. Always
        # convert a nonzero gesture into at least one scrolling unit.
        if getattr(event, "num", None) == 4:
            return -3
        if getattr(event, "num", None) == 5:
            return 3
        delta = int(getattr(event, "delta", 0) or 0)
        if delta == 0:
            return 0
        magnitude = max(1, abs(delta) // 120)
        return (-1 if delta > 0 else 1) * magnitude * 3

    def _route_trackpad_scroll(self, event):
        """Route wheel/trackpad scrolling according to pointer location."""
        try:
            pointer_widget = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            pointer_widget = None

        field_text = self.profile_field_text
        if (
            field_text is not None
            and field_text.winfo_exists()
            and self._widget_is_descendant(pointer_widget, field_text)
        ):
            units = self._wheel_units(event)
            if units:
                field_text.yview_scroll(units, "units")
            return "break"

        # Labels and frames inside the lower field box are siblings rather than
        # descendants of the Text widget. Use the field box's screen rectangle
        # as a fallback hit test so precision-trackpad events are still routed
        # to the field list.
        if field_text is not None and field_text.winfo_exists():
            try:
                x0 = field_text.winfo_rootx()
                y0 = field_text.winfo_rooty()
                x1 = x0 + field_text.winfo_width()
                y1 = y0 + field_text.winfo_height()
                if x0 <= event.x_root <= x1 and y0 <= event.y_root <= y1:
                    units = self._wheel_units(event)
                    if units:
                        field_text.yview_scroll(units, "units")
                    return "break"
            except Exception:
                pass

        if self._widget_is_descendant(pointer_widget, self.canvas):
            units = self._wheel_units(event)
            if units:
                self.canvas.yview_scroll(units, "units")
            return "break"

        # Do not consume events over the left JSON editor or other controls.
        return None

    @staticmethod
    def _scroll_text_with_wheel(widget: tk.Text, event) -> str:
        """Scroll the Text widget currently under the mouse pointer."""
        units = App._wheel_units(event)
        if units:
            widget.yview_scroll(units, "units")
        return "break"

    def _bind_text_mousewheel(self, widget: tk.Text) -> None:
        """Bind Windows/macOS and Linux mouse-wheel events to a Text widget."""
        widget.bind(
            "<MouseWheel>",
            lambda event, target=widget: self._scroll_text_with_wheel(target, event),
        )
        widget.bind(
            "<Button-4>",
            lambda event, target=widget: self._scroll_text_with_wheel(target, event),
        )
        widget.bind(
            "<Button-5>",
            lambda event, target=widget: self._scroll_text_with_wheel(target, event),
        )

    def code_box(self, parent, value: Any) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=4)
        text = tk.Text(
            frame, height=min(18, max(4, pretty(value).count("\n") + 1)),
            wrap="none", font=("Consolas", 9),
            background="#0f172a", foreground="#e2e8f0",
            padx=7, pady=7
        )
        v = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        h = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=v.set, xscrollcommand=h.set)
        self._bind_text_mousewheel(text)
        text.grid(row=0, column=0, sticky="nsew")
        v.grid(row=0, column=1, sticky="ns")
        h.grid(row=1, column=0, sticky="ew")
        frame.grid_columnconfigure(0, weight=1)
        text.insert("1.0", pretty(value))
        text.configure(state="disabled")

    def _insert_profile_fields(self, text_widget: tk.Text, fields: list[dict[str, Any]]) -> None:
        text_widget.configure(state="normal")
        text_widget.delete("1.0", "end")

        if not fields:
            text_widget.insert("end", "No fields are present in this section.\n", "body")
        else:
            for index, field_info in enumerate(fields, start=1):
                path = field_info["path"]
                text_widget.insert("end", f"{index}. {path}\n", "field_path")
                text_widget.insert("end", field_info["description"] + "\n", "body")

                values = field_info["values"]
                current = values[0] if len(values) == 1 else values
                text_widget.insert("end", "Current value: ", "label")
                text_widget.insert("end", _compact_value(current, 520) + "\n", "current")

                text_widget.insert("end", "JSON type: ", "label")
                text_widget.insert("end", field_info["type"] + "\n", "body")

                choices = field_info.get("choices", [])
                if choices:
                    text_widget.insert("end", "Accepted/known values", "label")
                    status = field_info.get("choice_status", "")
                    if status:
                        text_widget.insert("end", f" ({status})", "muted")
                    text_widget.insert("end", ": ", "label")
                    text_widget.insert(
                        "end",
                        ", ".join(json.dumps(v, ensure_ascii=False) for v in choices) + "\n",
                        "choice",
                    )

                aliases = field_info.get("aliases", [])
                if aliases:
                    text_widget.insert("end", "Aliases: ", "label")
                    text_widget.insert("end", "; ".join(aliases) + "\n", "body")

                constraints = field_info.get("constraints", "")
                if constraints:
                    text_widget.insert("end", "Constraints/use: ", "label")
                    text_widget.insert("end", constraints + "\n", "body")

                note = field_info.get("note", "")
                if note:
                    text_widget.insert("end", "Repository note: ", "label")
                    text_widget.insert("end", note + "\n", "warning")

                text_widget.insert("end", "\n")
                if index < len(fields):
                    text_widget.insert("end", "─" * 72 + "\n\n", "separator")

        text_widget.configure(state="disabled")

    def show_help(self, key: str, force: bool = False) -> None:
        original_key = key
        canonical_key = ALIASES.get(key, key)
        profile_key = original_key

        if not force and profile_key == self._current_help_key:
            return

        self._current_help_key = profile_key
        data = build_repository_profile(
            original_key,
            self.editor.get(),
            background_presets=self.background_presets,
            source_index=self.repository_sources,
        )

        self.clear_help()
        ttk.Label(
            self.inner,
            text=data["title"],
            font=("Segoe UI", 16, "bold"),
            wraplength=560,
            justify="left",
        ).pack(anchor="w", pady=(0, 7))

        summary = ttk.LabelFrame(self.inner, text="What this section controls", padding=8)
        summary.pack(fill="x", pady=5)
        ttk.Label(
            summary,
            text=data["summary"],
            wraplength=530,
            justify="left",
        ).pack(anchor="w")

        source_box = ttk.LabelFrame(self.inner, text="Repository support/status", padding=8)
        source_box.pack(fill="x", pady=5)
        ttk.Label(
            source_box,
            text=data["source_status"],
            wraplength=530,
            justify="left",
            foreground="#7c2d12",
        ).pack(anchor="w")

        fields_frame = ttk.LabelFrame(
            self.inner,
            text=f"All fields in this section ({len(data['fields'])})",
            padding=8,
        )
        fields_frame.pack(fill="both", expand=True, pady=5)
        fields_frame.grid_rowconfigure(0, weight=1)
        fields_frame.grid_columnconfigure(0, weight=1)

        field_text = tk.Text(
            fields_frame,
            height=31,
            wrap="word",
            font=("Segoe UI", 9),
            background="#ffffff",
            foreground="#1f2937",
            padx=8,
            pady=8,
            relief="solid",
            borderwidth=1,
        )
        field_scroll = ttk.Scrollbar(
            fields_frame,
            orient="vertical",
            command=field_text.yview,
        )
        field_text.configure(yscrollcommand=field_scroll.set)

        # Bind directly to the white field-list Text widget. Tk's native Text
        # class binding can consume precision-touchpad events before the
        # application-level bind_all handler sees them, so direct binding is
        # required when the pointer is over the white content area itself.
        self._bind_text_mousewheel(field_text)

        self.profile_field_text = field_text
        field_text.grid(row=0, column=0, sticky="nsew")
        field_scroll.grid(row=0, column=1, sticky="ns")

        field_text.tag_configure("field_path", font=("Consolas", 10, "bold"), foreground="#1d4ed8")
        field_text.tag_configure("label", font=("Segoe UI", 9, "bold"), foreground="#111827")
        field_text.tag_configure("body", font=("Segoe UI", 9), foreground="#344054")
        field_text.tag_configure("current", font=("Consolas", 9), foreground="#166534")
        field_text.tag_configure("choice", font=("Consolas", 9), foreground="#6b21a8")
        field_text.tag_configure("warning", font=("Segoe UI", 9), foreground="#9a3412")
        field_text.tag_configure("muted", font=("Segoe UI", 8, "italic"), foreground="#667085")
        field_text.tag_configure("separator", foreground="#cbd5e1")
        self._insert_profile_fields(field_text, data["fields"])

        sweep = ttk.LabelFrame(self.inner, text="Sweep guidance", padding=8)
        sweep.pack(fill="x", pady=5)
        ttk.Label(
            sweep,
            text=data["sweep_text"],
            wraplength=530,
            justify="left",
        ).pack(anchor="w")

        one = ttk.LabelFrame(self.inner, text="Current single-run section", padding=8)
        one.pack(fill="x", pady=5)
        self.code_box(one, data["single"])

        self.canvas.yview_moveto(0)
        tree_key = canonical_key if self.tree.exists(canonical_key) else profile_key
        if self.tree.exists(tree_key) and self.tree.selection() != (tree_key,):
            self.tree.selection_set(tree_key)
            self.tree.see(tree_key)

    def changed(self) -> None:
        if self._loading_editor:
            return

        self.dirty = True
        line = self.editor.line()
        self.line_var.set(f"Line {line}")
        self.update_title()

        # Rebuilding the full right pane for every keypress made the original
        # editor sluggish. Wait briefly and update only after typing pauses.
        if self._editor_update_job is not None:
            self.after_cancel(self._editor_update_job)
        self._editor_update_job = self.after(450, self._update_context_help)

    def _update_context_help(self) -> None:
        self._editor_update_job = None
        line = self.editor.line()
        result = section_range_at_line(self.editor.get(), line)
        if result is None:
            return

        key, start_line, end_line = result
        self.editor.highlight_selected_section(start_line, end_line)

        # Typing and cursor movement should remain cheap. Rebuild the right pane
        # only when the cursor actually enters a different top-level section.
        if key == self._selected_section_key:
            return

        self._selected_section_key = key
        self.show_help(ALIASES.get(key, key))

    def update_title(self) -> None:
        name = self.current.name if self.current else "Untitled"
        mark = " *" if self.dirty else ""
        self.title(f"{APP_TITLE} — {name}{mark}")
        self.file_var.set(str(self.current) if self.current else "Unsaved configuration")

    def load_template(self, name: str, confirm: bool = True) -> None:
        if confirm and self.dirty and self.editor.get().strip():
            if not messagebox.askyesno("Replace JSON", "Replace the current unsaved JSON with this template?"):
                return
        self._loading_editor = True
        try:
            self.editor.set(pretty(TEMPLATES[name]()))
        finally:
            self._loading_editor = False
        self.current = None
        self.dirty = True
        self.template_var.set(name)
        self.status_var.set(f"Loaded template: {name}")
        self.update_title()
        self.refresh_tree()
        self.show_help("root", force=True)

    def open_file(self) -> None:
        initial = self.repo / CONFIG_DIR
        if not initial.exists():
            initial = self.repo
        name = filedialog.askopenfilename(
            parent=self, initialdir=str(initial),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if name:
            self.load_file(Path(name))

    def load_file(self, path: Path, confirm: bool = True) -> None:
        if confirm and self.dirty and self.editor.get().strip():
            if not messagebox.askyesno("Unsaved changes", "Discard current changes?"):
                return
        try:
            value = path.read_text(encoding="utf-8")
            json.loads(value)
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))
            return
        self._loading_editor = True
        try:
            self.editor.set(value)
        finally:
            self._loading_editor = False
        self.current = path.resolve()
        self.dirty = False
        self._selected_section_key = ""
        self.editor.highlight_selected_section(None, None)
        if self.current.parent == self.repo / CONFIG_DIR:
            self.config_picker_var.set(self.current.name)
        self.status_var.set(f"Opened {self.current.name}")
        self.update_title()
        self.refresh_tree()
        self.show_help("root", force=True)

    def validate(self, success: bool = True) -> bool:
        self.editor.text.tag_remove("error", "1.0", "end")
        try:
            value = json.loads(self.editor.get())
        except json.JSONDecodeError as exc:
            self.editor.error_line(exc.lineno)
            self.status_var.set(f"JSON error at line {exc.lineno}, column {exc.colno}: {exc.msg}")
            messagebox.showerror("Invalid JSON", f"Line {exc.lineno}, column {exc.colno}\n\n{exc.msg}")
            return False
        if not isinstance(value, dict):
            messagebox.showerror("Invalid JSON", "The top-level JSON value must be an object.")
            return False

        warnings = []
        for required in ("output", "optics", "tracking"):
            if required not in value:
                warnings.append(f"Missing top-level section: {required}")

        top = value.get("tiling", {}) if isinstance(value.get("tiling"), dict) else {}
        track = value.get("tracking", {}) if isinstance(value.get("tracking"), dict) else {}
        nested = track.get("tiling", {}) if isinstance(track.get("tiling"), dict) else {}
        if top.get("matched_filter_backend") and nested.get("matched_filter_backend") and top["matched_filter_backend"] != nested["matched_filter_backend"]:
            warnings.append("tiling.matched_filter_backend differs from tracking.tiling.matched_filter_backend")

        policy = value.get("output_policy", {})
        if isinstance(policy, dict) and policy.get("enabled") is False and policy.get("cleanup_intermediate_outputs") is True:
            warnings.append("Full-output mode is selected but cleanup_intermediate_outputs is true")

        if warnings:
            self.status_var.set("JSON valid with warnings")
            if success:
                messagebox.showwarning("JSON valid with warnings", "\n\n".join(warnings))
        else:
            self.status_var.set("JSON is valid")
            if success:
                messagebox.showinfo("JSON validation", "The JSON is syntactically valid.")
        return True

    def format_json(self) -> None:
        try:
            value = json.loads(self.editor.get())
        except json.JSONDecodeError as exc:
            self.editor.error_line(exc.lineno)
            messagebox.showerror("Cannot format", f"Line {exc.lineno}, column {exc.colno}\n\n{exc.msg}")
            return
        self.editor.set(pretty(value))
        self.dirty = True
        self.status_var.set("JSON formatted")
        self.update_title()

    def save(self) -> bool:
        if self.current is None:
            return self.save_as()
        if not self.validate(success=False):
            return False
        try:
            self.current.parent.mkdir(parents=True, exist_ok=True)
            self.current.write_text(self.editor.get().rstrip() + "\n", encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc))
            return False
        self.dirty = False
        self.status_var.set(f"Saved {self.current.name}")
        self.update_title()
        return True

    def save_as(self) -> bool:
        initial = self.repo / CONFIG_DIR
        initial.mkdir(parents=True, exist_ok=True)
        name = filedialog.asksaveasfilename(
            parent=self, initialdir=str(initial), defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not name:
            return False
        self.current = Path(name).resolve()
        saved = self.save()
        if saved:
            self.refresh_config_files()
        return saved

    def python_executable(self) -> Path:
        for name in VENV_NAMES:
            candidate = self.repo / name / "Scripts" / "python.exe"
            if candidate.exists():
                return candidate
        return Path(sys.executable)

    def run(self) -> None:
        if self.process is not None and self.process.poll() is None:
            messagebox.showwarning("Already running", "An experiment is already running.")
            return
        if not self.validate(success=False) or not self.save():
            return
        runner = self.repo / "experiments" / "run_snr_auc_sweep.py"
        if not runner.exists():
            messagebox.showerror("Runner not found", "Place this program at the repository root.")
            return
        assert self.current is not None
        command = [
            str(self.python_executable()), "-m", "experiments.run_snr_auc_sweep",
            "--config", str(self.current)
        ]

        window = tk.Toplevel(self)
        window.title("OPIR experiment output")
        window.geometry("1060x650")
        window.grid_rowconfigure(0, weight=1)
        window.grid_columnconfigure(0, weight=1)
        output = tk.Text(window, wrap="none", font=("Consolas", 10), background="#111827", foreground="#e5e7eb")
        v = ttk.Scrollbar(window, orient="vertical", command=output.yview)
        h = ttk.Scrollbar(window, orient="horizontal", command=output.xview)
        output.configure(yscrollcommand=v.set, xscrollcommand=h.set)
        output.grid(row=0, column=0, sticky="nsew")
        v.grid(row=0, column=1, sticky="ns")
        h.grid(row=1, column=0, sticky="ew")
        output.insert("end", "Command:\n" + subprocess.list2cmdline(command) + "\n\n")
        self.status_var.set("Experiment running...")

        def append(text: str) -> None:
            if output.winfo_exists():
                output.insert("end", text)
                output.see("end")

        def worker() -> None:
            try:
                self.process = subprocess.Popen(
                    command, cwd=str(self.repo), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1
                )
                assert self.process.stdout is not None
                for line in self.process.stdout:
                    self.after(0, append, line)
                code = self.process.wait()
                self.after(0, append, f"\nExperiment exited with code {code}.\n")
                self.after(0, self.status_var.set, "Experiment completed" if code == 0 else f"Experiment failed: {code}")
            except Exception as exc:
                self.after(0, append, f"\nERROR: {exc}\n")
                self.after(0, self.status_var.set, "Experiment failed to start")
            finally:
                self.process = None

        threading.Thread(target=worker, daemon=True).start()

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            self.status_var.set("No experiment is running")
            return
        self.process.terminate()
        self.status_var.set("Stopping experiment...")

    def close(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if not messagebox.askyesno("Experiment running", "Stop the experiment and exit?"):
                return
            self.stop()
        if self.dirty:
            choice = messagebox.askyesnocancel("Unsaved changes", "Save before exiting?")
            if choice is None:
                return
            if choice and not self.save():
                return
        self.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument(
        "config",
        nargs="?",
        help="JSON file to open as the editable template.",
    )
    parser.add_argument(
        "--template",
        dest="template",
        help="Alternative named JSON template path to open.",
    )
    args = parser.parse_args()

    chosen = args.config or args.template
    initial = Path(chosen).expanduser() if chosen else None
    if initial is not None and not initial.is_absolute():
        initial = Path.cwd() / initial

    if initial is not None and not initial.exists():
        parser.error(f"JSON template was not found: {initial}")

    App(initial).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
