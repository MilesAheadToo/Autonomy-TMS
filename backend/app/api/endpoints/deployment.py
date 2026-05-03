"""
Deployment Pipeline API — read-only CSV download surface.

The orchestration routes (POST /pipelines, list, detail, steps, cancel)
were retired in PR-5.B 2026-05-03 — see
docs/TWIN_PR5_CALLER_MIGRATION_AUDIT.md. The orchestrator they drove
(``DeploymentPipelineService`` + the SCP-shape Monte Carlo /
training-data-converter pipeline) was SCP-fork residue producing
training records no TMS TRM consumes.

What survives:
- ``GET /csvs/{pipeline_id}``           — list day1 / day2 SAP CSV ZIPs
- ``GET /csvs/{pipeline_id}/{csv_type}`` — download a specific ZIP

These read pre-existing ``DeploymentPipelineRun`` rows; new rows are
only created by historical pipelines or by future TMS-shape
training services that we have not yet built. The
``deployment_pipeline_run`` table is preserved as the lookup index.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.api.deps import get_current_user
from app.models.user import User
from app.models.deployment_pipeline import DeploymentPipelineRun

router = APIRouter()


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/csvs/{pipeline_id}")
async def list_csvs(
    pipeline_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List generated CSV ZIP files for a pipeline."""
    result = await db.execute(
        select(DeploymentPipelineRun)
        .where(DeploymentPipelineRun.id == pipeline_id)
    )
    pipeline = result.scalar_one_or_none()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    results = pipeline.results or {}
    csvs = []

    # Check for Day 1 ZIP
    step6 = results.get("step_6", {})
    if step6.get("zip_path"):
        zip_path = Path(step6["zip_path"])
        csvs.append({
            "type": "day1",
            "filename": zip_path.name,
            "path": str(zip_path),
            "exists": zip_path.exists(),
        })

    # Check for Day 2 ZIP
    step7 = results.get("step_7", {})
    if step7.get("zip_path"):
        zip_path = Path(step7["zip_path"])
        csvs.append({
            "type": "day2",
            "filename": zip_path.name,
            "path": str(zip_path),
            "exists": zip_path.exists(),
            "profile": step7.get("profile"),
        })

    return {"pipeline_id": pipeline_id, "csvs": csvs}


@router.get("/csvs/{pipeline_id}/{csv_type}")
async def download_csv(
    pipeline_id: int,
    csv_type: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download a generated CSV ZIP file (day1 or day2)."""
    result = await db.execute(
        select(DeploymentPipelineRun)
        .where(DeploymentPipelineRun.id == pipeline_id)
    )
    pipeline = result.scalar_one_or_none()
    if not pipeline:
        raise HTTPException(status_code=404, detail="Pipeline not found")

    results = pipeline.results or {}

    if csv_type == "day1":
        step_result = results.get("step_6", {})
    elif csv_type == "day2":
        step_result = results.get("step_7", {})
    else:
        raise HTTPException(status_code=400, detail="csv_type must be 'day1' or 'day2'")

    zip_path = step_result.get("zip_path")
    if not zip_path:
        raise HTTPException(status_code=404, detail=f"No {csv_type} CSV available")

    zip_file = Path(zip_path)
    if not zip_file.exists():
        raise HTTPException(status_code=404, detail=f"CSV file not found: {zip_file.name}")

    return FileResponse(
        path=str(zip_file),
        filename=zip_file.name,
        media_type="application/zip",
    )
