"""系统能力探测接口。"""
from __future__ import annotations

from fastapi import APIRouter, Query

from ..core.probe import probe

router = APIRouter()


@router.get("/system/probe")
def system_probe(force: bool = Query(default=False)):
    return probe(force=force)


@router.get("/system/capabilities")
def system_capabilities():
    data = probe()
    return {
        "engines": data["engines"],
        "tools": data["tools"],
        "device": data["device"],
        "recommendation": data["recommendation"],
        "engine_mode": data["engine_mode"],
    }
