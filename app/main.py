"""FastAPI app: the /api routes, plus the built UI on /."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import (
    AppConfig,
    ConfigError,
    ConfigStorageError,
    ConfigStore,
    UnknownGroupError,
    load_config,
    normalize_device,
)
from app.devices import DeviceManager
from app.models import (
    ApplyPresetRequest,
    ApplyResponse,
    BrightnessRequest,
    ConfigResponse,
    DeviceInfo,
    GroupCreate,
    GroupModel,
    GroupOrderRequest,
    GroupRename,
    GroupStatus,
    HealthResponse,
    MoveStrandRequest,
    Pattern,
    PreviewRequest,
    PreviewResponse,
    ReorderRequest,
    StrandConfig,
    StrandConfigResult,
    StrandCreate,
)
from app.pattern import led_colors
from app.presets import PresetError, PresetStorageError, PresetStore
from app.state import StateStore

logging.basicConfig(
    level=os.environ.get("DAPPLE_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("dapple")

REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_static_dir() -> Path | None:
    """Where the built UI lives: /app/static in the image, frontend/dist in dev."""
    candidates = [
        Path(p)
        for p in (
            os.environ.get("DAPPLE_STATIC_DIR"),
            REPO_ROOT / "static",
            REPO_ROOT / "frontend" / "dist",
        )
        if p
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    config: AppConfig = getattr(app.state, "config", None) or load_config()
    app.state.config = config
    if not hasattr(app.state, "manager"):
        app.state.manager = DeviceManager(config)
    if not hasattr(app.state, "strands"):
        app.state.strands = ConfigStore(config)
    if not hasattr(app.state, "presets"):
        app.state.presets = PresetStore(config.presets_path)
    if not hasattr(app.state, "group_state"):
        app.state.group_state = StateStore(config.state_path)
    if not app.state.presets.writable:
        log.warning("%s is not writable; presets cannot be saved", config.presets_path)
    log.info(
        "Config from %s: %d strand(s): %s",
        config.source,
        len(config.devices),
        ", ".join(
            f"{group.name}: " + ", ".join(d.name for d in group.devices) for group in config.groups
        )
        or "none",
    )
    # Strands that are simply switched off shouldn't hold up startup.
    asyncio.create_task(_startup_refresh(app))
    yield


async def _startup_refresh(app: FastAPI) -> None:
    try:
        for info in await app.state.manager.refresh():
            if info.reachable:
                log.info(
                    "%s @ %s: %s LEDs, %s, fw %s",
                    info.name,
                    info.host,
                    info.number_of_led,
                    info.led_profile,
                    info.fw_version,
                )
            else:
                log.warning("%s @ %s unreachable: %s", info.name, info.host, info.error)
    except Exception as exc:
        log.error("Startup refresh failed: %s", exc)


app = FastAPI(title="Dapple", version="0.2.2", lifespan=lifespan)


def manager(request: Request) -> DeviceManager:
    return request.app.state.manager


def presets(request: Request) -> PresetStore:
    return request.app.state.presets


def strands(request: Request) -> ConfigStore:
    return request.app.state.strands


def group_state(request: Request) -> StateStore:
    return request.app.state.group_state


@app.exception_handler(PresetError)
async def preset_error_handler(_request: Request, exc: PresetError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(PresetStorageError)
async def preset_storage_error_handler(_request: Request, exc: PresetStorageError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(UnknownGroupError)
async def unknown_group_handler(_request: Request, exc: UnknownGroupError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ConfigError)
async def config_error_handler(_request: Request, exc: ConfigError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(ConfigStorageError)
async def config_storage_error_handler(_request: Request, exc: ConfigStorageError) -> JSONResponse:
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def _response(results) -> ApplyResponse:
    return ApplyResponse(ok=all(result.ok for result in results), results=results)


# ---- status ---------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    results = await manager(request).health()
    return HealthResponse(ok=all(result.ok for result in results), devices=results)


@app.get("/api/devices", response_model=list[DeviceInfo])
async def devices(request: Request) -> list[DeviceInfo]:
    return await manager(request).info(with_live_state=True)


@app.post("/api/devices/refresh", response_model=list[DeviceInfo])
async def refresh_devices(request: Request) -> list[DeviceInfo]:
    return await manager(request).refresh()


# ---- groups: status and control -------------------------------------------


def _group_status(request: Request, group) -> GroupStatus:
    """One group as it is right now — configuration, layout and last-applied."""
    return GroupStatus(
        id=group.id,
        name=group.name,
        strands=[
            StrandConfig(
                name=device.config.name,
                host=device.config.host,
                number_of_led=device.config.number_of_led,
                led_profile=device.config.led_profile,
            )
            for device in group.devices
        ],
        total_leds=group.total_leds(),
        segments=group.segments(),
        reachable=group.reachable(),
        state=group_state(request).get(group.id),
    )


def _group_or_503(request: Request, group_id: str):
    """The group's devices, or a 503 — an empty group has nothing to apply to."""
    group = manager(request).group(group_id)
    if not group.devices:
        raise HTTPException(status_code=503, detail=f"Group {group.name!r} has no strands")
    return group


@app.get("/api/groups", response_model=list[GroupStatus])
async def list_groups(request: Request) -> list[GroupStatus]:
    return [_group_status(request, group) for group in manager(request).groups]


@app.get("/api/groups/{group_id}", response_model=GroupStatus)
async def get_group(request: Request, group_id: str) -> GroupStatus:
    return _group_status(request, manager(request).group(group_id))


@app.post("/api/groups/{group_id}/apply", response_model=ApplyResponse)
async def apply_to_group(request: Request, group_id: str, pattern: Pattern) -> ApplyResponse:
    group = _group_or_503(request, group_id)
    results = await manager(request).apply_pattern(group.id, pattern)
    group_state(request).record(group.id, pattern)
    return _response(results)


@app.post("/api/groups/{group_id}/preset", response_model=ApplyResponse)
async def apply_preset_to_group(
    request: Request, group_id: str, payload: ApplyPresetRequest
) -> ApplyResponse:
    """What Home Assistant calls.

    The preset name travels in the body rather than the path: names contain
    spaces (``Warm white`` is one of the seeded defaults) and nesting a
    user-chosen string two segments deep is an encoding bug waiting to happen.
    """
    pattern = presets(request).get(payload.name)
    group = _group_or_503(request, group_id)
    results = await manager(request).apply_pattern(group.id, pattern)
    group_state(request).record(group.id, pattern, preset=payload.name)
    return _response(results)


@app.post("/api/groups/{group_id}/brightness", response_model=ApplyResponse)
async def set_group_brightness(
    request: Request, group_id: str, payload: BrightnessRequest = Body(...)
) -> ApplyResponse:
    group = _group_or_503(request, group_id)
    results = await manager(request).set_brightness(group.id, payload.value)
    group_state(request).set_brightness(group.id, payload.value)
    return _response(results)


@app.post("/api/groups/{group_id}/on", response_model=ApplyResponse)
async def turn_group_on(request: Request, group_id: str) -> ApplyResponse:
    group = _group_or_503(request, group_id)
    results = await manager(request).turn_on(group.id)
    group_state(request).set_power(group.id, True)
    return _response(results)


@app.post("/api/groups/{group_id}/off", response_model=ApplyResponse)
async def turn_group_off(request: Request, group_id: str) -> ApplyResponse:
    """Off leaves the pattern on the strand — it just stops showing it."""
    group = _group_or_503(request, group_id)
    results = await manager(request).turn_off(group.id)
    group_state(request).set_power(group.id, False)
    return _response(results)


@app.post("/api/preview", response_model=PreviewResponse)
async def preview(payload: PreviewRequest) -> PreviewResponse:
    """The pattern as LED colors. The UI computes this itself; this endpoint is
    the reference the JS port is tested against."""
    return PreviewResponse(leds=led_colors(payload.pattern, payload.num_leds, payload.offset))


# ---- presets --------------------------------------------------------------


@app.get("/api/presets", response_model=dict[str, Pattern])
async def list_presets(request: Request) -> dict[str, Pattern]:
    return presets(request).all()


@app.get("/api/presets/{name}", response_model=Pattern)
async def get_preset(request: Request, name: str) -> Pattern:
    return presets(request).get(name)


@app.put("/api/presets/{name}", response_model=Pattern)
async def put_preset(request: Request, name: str, pattern: Pattern) -> Pattern:
    try:
        return presets(request).save(name, pattern)
    except PresetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/presets/{name}")
async def delete_preset(request: Request, name: str) -> dict:
    presets(request).delete(name)
    return {"ok": True}


# ---- configuration: groups and strands ------------------------------------


def _as_model(device) -> StrandConfig:
    return StrandConfig(
        name=device.name,
        host=device.host,
        number_of_led=device.number_of_led,
        led_profile=device.led_profile,
    )


def _group_model(group) -> GroupModel:
    return GroupModel(
        id=group.id,
        name=group.name,
        strands=[_as_model(device) for device in group.devices],
    )


async def _resync(request: Request, host: str | None = None) -> StrandConfigResult | None:
    """Rebuild the device list, then probe one strand so the page can say
    straight away whether it answered."""
    handler = manager(request)
    handler.sync()
    if host is None:
        return None
    info = await handler.refresh_one(host)
    store = strands(request)
    return StrandConfigResult(
        config=_as_model(store.find(host)),
        group=store.group_of(host).id,
        info=info,
    )


@app.get("/api/config", response_model=ConfigResponse)
async def get_config(request: Request) -> ConfigResponse:
    store = strands(request)
    return ConfigResponse(
        groups=[_group_model(group) for group in store.groups],
        source=store.config.source,
        writable=store.writable,
        state_writable=group_state(request).writable,
        movie_frames=store.config.movie_frames,
        timeout=store.config.timeout,
    )


# ---- groups ---------------------------------------------------------------


@app.post("/api/config/groups", response_model=GroupModel, status_code=201)
async def create_group(request: Request, payload: GroupCreate) -> GroupModel:
    group = strands(request).create_group(payload.name)
    await _resync(request)
    return _group_model(group)


@app.put("/api/config/groups/order", response_model=list[GroupModel])
async def reorder_groups(request: Request, payload: GroupOrderRequest) -> list[GroupModel]:
    """Display order only — every group starts its pattern at its own first LED.

    Declared before ``/{group_id}``: FastAPI matches in declaration order, so
    the other way round this would be read as renaming a group called "order".
    (``order`` is also a reserved group id, so no such group can exist.)
    """
    groups = strands(request).reorder_groups(payload.ids)
    await _resync(request)
    return [_group_model(group) for group in groups]


@app.put("/api/config/groups/{group_id}", response_model=GroupModel)
async def rename_group(request: Request, group_id: str, payload: GroupRename) -> GroupModel:
    """Renaming never changes the id, so anything addressing this group keeps working."""
    group = strands(request).rename_group(group_id, payload.name)
    await _resync(request)
    return _group_model(group)


@app.delete("/api/config/groups/{group_id}")
async def delete_group(request: Request, group_id: str) -> dict:
    strands(request).delete_group(group_id)
    group_state(request).forget(group_id)
    await _resync(request)
    return {"ok": True}


@app.put("/api/config/groups/{group_id}/strands/order", response_model=list[StrandConfig])
async def reorder_group_strands(
    request: Request, group_id: str, payload: ReorderRequest
) -> list[StrandConfig]:
    """Physical order within one group — this is what sets the seam."""
    devices = strands(request).reorder(group_id, payload.hosts)
    await _resync(request)
    return [_as_model(device) for device in devices]


# ---- strands --------------------------------------------------------------


@app.post("/api/config/strands", response_model=StrandConfigResult, status_code=201)
async def add_strand(request: Request, strand: StrandCreate) -> StrandConfigResult:
    """Add a strand, then probe it — a typo in the address should show up here
    rather than the first time someone hits Apply.

    With no group it gets one of its own, so adding a strand stays a single step.
    """
    store = strands(request)
    device = normalize_device(
        strand.name,
        strand.host,
        strand.number_of_led,
        strand.led_profile,
        fallback_name=store.next_name(),
    )
    store.add(device, strand.group)
    return await _resync(request, device.host)


@app.put("/api/config/strands/{host}/group", response_model=StrandConfigResult)
async def move_strand(
    request: Request, host: str, payload: MoveStrandRequest
) -> StrandConfigResult:
    """Move a strand to another group, or reposition it within its own."""
    device = strands(request).move(host, payload.group, payload.index)
    return await _resync(request, device.host)


@app.put("/api/config/strands/{host}", response_model=StrandConfigResult)
async def update_strand(request: Request, host: str, strand: StrandConfig) -> StrandConfigResult:
    store = strands(request)
    existing = store.find(host)
    updated = store.update(
        host,
        normalize_device(
            strand.name,
            strand.host,
            strand.number_of_led,
            strand.led_profile,
            fallback_name=existing.name,
        ),
    )
    return await _resync(request, updated.host)


@app.delete("/api/config/strands/{host}")
async def delete_strand(request: Request, host: str) -> dict:
    strands(request).delete(host)
    await _resync(request)
    return {"ok": True}


# ---- UI -------------------------------------------------------------------

_static_dir = resolve_static_dir()
if _static_dir:
    log.info("Serving UI from %s", _static_dir)
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="ui")
else:

    @app.get("/", response_class=HTMLResponse)
    async def missing_ui() -> HTMLResponse:
        return HTMLResponse(
            "<h1>Dapple</h1><p>No built UI found. Run <code>npm --prefix frontend "
            "install &amp;&amp; npm --prefix frontend run build</code>, or use the "
            "Docker image. The API is up at <a href='/docs'>/docs</a>.</p>",
            status_code=200,
        )
