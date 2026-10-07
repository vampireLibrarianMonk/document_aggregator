"""Tests for container-aware worker sizing (Phase C).

The core risk this guards: in k8s, os.cpu_count() reports the NODE's cores, not
the pod's CPU limit, so sizing a pool by it oversubscribes a limited pod and
gets CFS-throttled. available_cores() must prefer the cgroup quota; worker_count
applies the cores-1 policy with an env override.
"""
from __future__ import annotations

import app.json_alignment.concurrency as cc


def _fake_file(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return p


# -- cgroup v2 ---------------------------------------------------------------

def test_cgroup_v2_quota_derives_cores(tmp_path, monkeypatch):
    # 150000 / 100000 = 1.5 cores -> ceil 2
    f = _fake_file(tmp_path, "cpu.max", "150000 100000")
    monkeypatch.setattr(cc, "_CGROUP_V2", f)
    # ensure v1 is absent so v2 is the source
    monkeypatch.setattr(cc, "_CGROUP_V1_QUOTA", tmp_path / "nope_q")
    monkeypatch.setattr(cc, "_CGROUP_V1_PERIOD", tmp_path / "nope_p")
    assert cc.available_cores() == 2


def test_cgroup_v2_max_means_unlimited_falls_back(tmp_path, monkeypatch):
    f = _fake_file(tmp_path, "cpu.max", "max 100000")
    monkeypatch.setattr(cc, "_CGROUP_V2", f)
    monkeypatch.setattr(cc, "_CGROUP_V1_QUOTA", tmp_path / "nope_q")
    monkeypatch.setattr(cc, "_CGROUP_V1_PERIOD", tmp_path / "nope_p")
    monkeypatch.setattr(cc.os, "cpu_count", lambda: 8)
    assert cc.available_cores() == 8  # unlimited -> node cpu_count fallback


def test_cgroup_v2_exact_cores(tmp_path, monkeypatch):
    f = _fake_file(tmp_path, "cpu.max", "400000 100000")  # 4.0 cores
    monkeypatch.setattr(cc, "_CGROUP_V2", f)
    monkeypatch.setattr(cc, "_CGROUP_V1_QUOTA", tmp_path / "nope_q")
    monkeypatch.setattr(cc, "_CGROUP_V1_PERIOD", tmp_path / "nope_p")
    assert cc.available_cores() == 4


# -- cgroup v1 ---------------------------------------------------------------

def test_cgroup_v1_quota_derives_cores(tmp_path, monkeypatch):
    q = _fake_file(tmp_path, "cfs_quota_us", "200000")   # 2.0 cores
    p = _fake_file(tmp_path, "cfs_period_us", "100000")
    monkeypatch.setattr(cc, "_CGROUP_V2", tmp_path / "no_v2")
    monkeypatch.setattr(cc, "_CGROUP_V1_QUOTA", q)
    monkeypatch.setattr(cc, "_CGROUP_V1_PERIOD", p)
    assert cc.available_cores() == 2


def test_cgroup_v1_unlimited_quota_falls_back(tmp_path, monkeypatch):
    q = _fake_file(tmp_path, "cfs_quota_us", "-1")  # unlimited
    p = _fake_file(tmp_path, "cfs_period_us", "100000")
    monkeypatch.setattr(cc, "_CGROUP_V2", tmp_path / "no_v2")
    monkeypatch.setattr(cc, "_CGROUP_V1_QUOTA", q)
    monkeypatch.setattr(cc, "_CGROUP_V1_PERIOD", p)
    monkeypatch.setattr(cc.os, "cpu_count", lambda: 6)
    assert cc.available_cores() == 6


# -- bare-metal fallback -----------------------------------------------------

def test_no_cgroup_falls_back_to_cpu_count(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "_CGROUP_V2", tmp_path / "no_v2")
    monkeypatch.setattr(cc, "_CGROUP_V1_QUOTA", tmp_path / "no_q")
    monkeypatch.setattr(cc, "_CGROUP_V1_PERIOD", tmp_path / "no_p")
    monkeypatch.setattr(cc.os, "cpu_count", lambda: 12)
    assert cc.available_cores() == 12


def test_available_cores_floor_is_one(tmp_path, monkeypatch):
    monkeypatch.setattr(cc, "_CGROUP_V2", tmp_path / "no_v2")
    monkeypatch.setattr(cc, "_CGROUP_V1_QUOTA", tmp_path / "no_q")
    monkeypatch.setattr(cc, "_CGROUP_V1_PERIOD", tmp_path / "no_p")
    monkeypatch.setattr(cc.os, "cpu_count", lambda: None)  # unknown
    assert cc.available_cores() == 1


# -- worker_count policy -----------------------------------------------------

def test_worker_count_is_cores_minus_one(monkeypatch):
    monkeypatch.setattr(cc, "available_cores", lambda: 8)
    monkeypatch.delenv("JSON_ALIGNMENT_MAX_WORKERS", raising=False)
    assert cc.worker_count() == 7


def test_worker_count_floor_is_one(monkeypatch):
    monkeypatch.setattr(cc, "available_cores", lambda: 1)
    monkeypatch.delenv("JSON_ALIGNMENT_MAX_WORKERS", raising=False)
    assert cc.worker_count() == 1  # 1 - 1 floored to 1


def test_worker_count_env_override_wins(monkeypatch):
    monkeypatch.setattr(cc, "available_cores", lambda: 32)
    monkeypatch.setenv("JSON_ALIGNMENT_MAX_WORKERS", "3")
    assert cc.worker_count() == 3


def test_worker_count_maximum_caps_result(monkeypatch):
    monkeypatch.setattr(cc, "available_cores", lambda: 16)
    monkeypatch.delenv("JSON_ALIGNMENT_MAX_WORKERS", raising=False)
    # 16 - 1 = 15, but only 4 clusters -> cap at 4
    assert cc.worker_count(maximum=4) == 4


def test_worker_count_bad_env_ignored(monkeypatch):
    monkeypatch.setattr(cc, "available_cores", lambda: 4)
    monkeypatch.setenv("JSON_ALIGNMENT_MAX_WORKERS", "not-a-number")
    assert cc.worker_count() == 3  # falls back to cores-1
