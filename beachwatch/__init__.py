"""净滩台账：解析、分析、公开报告与旧栏目更正。"""
from .model import load_records
from .parser import parse_all
from .analysis import (
    build_observations, build_states, compare_all, combined_weights,
    combine_intervals, display, criteria_by_metric,
)
from .report import build_report, render_text

__all__ = [
    'load_records', 'parse_all', 'build_observations', 'build_states',
    'compare_all', 'combined_weights', 'combine_intervals', 'display',
    'criteria_by_metric',
    'build_report', 'render_text',
]
