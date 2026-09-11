"""
Named rectangles on the instrument panel, used to reclassify a raw
"Instruments" gaze sample into a specific dial (see gaze_point_x/y in
eye_tracking_data). One shared layout across all sessions.
"""
from fastapi import APIRouter, HTTPException

from .. import db
from ..schemas import AoiZone, AoiZoneCreate

router = APIRouter(prefix="/aoi-zones", tags=["aoi-zones"])


@router.get("", response_model=list[AoiZone])
async def list_zones():
    async with db.acquire() as conn:
        rows = await conn.fetch("select id, name, x1, y1, x2, y2 from aoi_zones order by id")
    return [dict(r) for r in rows]


@router.post("", response_model=AoiZone)
async def create_zone(body: AoiZoneCreate):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "insert into aoi_zones (name, x1, y1, x2, y2) values ($1,$2,$3,$4,$5) "
            "returning id, name, x1, y1, x2, y2",
            body.name, body.x1, body.y1, body.x2, body.y2,
        )
    return dict(row)


@router.delete("/{zone_id}")
async def delete_zone(zone_id: int):
    async with db.acquire() as conn:
        r = await conn.execute("delete from aoi_zones where id=$1", zone_id)
    if r.endswith("0"):
        raise HTTPException(404, "Zone not found")
    return {"ok": True}
