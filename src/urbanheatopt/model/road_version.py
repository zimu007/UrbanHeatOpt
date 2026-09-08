"""Versioned road-atomic joint model; the frozen competition core is untouched."""

MODEL_VERSION = "road_joint_v2.0.0-dev"
# Relocation-only hash, after tools/verify_layout.py verified all function bodies.
# The original V1 hash and source are retained in the baseline manifest/Git tag.
# The original relocated V1 hash remains recorded in core_model_freeze.yaml.
# This executable hash is the teacher-confirmed 2026-09-07 capacity-margin
# revision shared by the reference, road and compact regression paths.
FROZEN_LEGACY_CORE_SHA256 = "77a33f9f71e93bf71898599818a9ebc18d04f1330a32634161b5748f81777210"
