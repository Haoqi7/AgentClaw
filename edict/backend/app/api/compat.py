"""Legacy API compatibility layer for the existing dashboard frontend."""

from __future__ import annotations

import importlib.util
import subprocess
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

_legacy_module = None
_legacy_lock = threading.Lock()


def _find_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "dashboard" / "server.py").exists():
            return parent
    raise RuntimeError("cannot find repository root containing dashboard/server.py")


def _load_legacy_dashboard():
    global _legacy_module
    if _legacy_module is not None:
        return _legacy_module
    with _legacy_lock:
        if _legacy_module is not None:
            return _legacy_module
        server_path = _find_repo_root() / "dashboard" / "server.py"
        spec = importlib.util.spec_from_file_location("edict_legacy_dashboard_server", server_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load legacy dashboard module: {server_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _legacy_module = module
        return module


class SetModelBody(BaseModel):
    agentId: str
    model: str


class DispatchChannelBody(BaseModel):
    channel: str


class AgentWakeBody(BaseModel):
    agentId: str
    message: str = ""


class TaskActionBody(BaseModel):
    taskId: str
    action: str
    reason: str = ""


class ReviewActionBody(BaseModel):
    taskId: str
    action: str
    comment: str = ""


class AdvanceStateBody(BaseModel):
    taskId: str
    comment: str = ""


class ArchiveTaskBody(BaseModel):
    taskId: str | None = None
    archived: bool = True
    archiveAllDone: bool = False


class SchedulerScanBody(BaseModel):
    thresholdSec: int = 180


class SchedulerActionBody(BaseModel):
    taskId: str
    reason: str = ""


class AddSkillBody(BaseModel):
    agentId: str
    skillName: str
    description: str = ""
    trigger: str = ""


class AddRemoteSkillBody(BaseModel):
    agentId: str
    skillName: str
    sourceUrl: str
    description: str = ""


class RemoteSkillBody(BaseModel):
    agentId: str
    skillName: str


class CreateTaskBody(BaseModel):
    title: str
    org: str = "中书省"
    official: str = "中书令"
    priority: str = "normal"
    templateId: str = ""
    params: dict | None = None
    targetDept: str = ""


class CourtStartBody(BaseModel):
    topic: str
    officials: list[str]
    taskId: str = ""


class CourtAdvanceBody(BaseModel):
    sessionId: str
    userMessage: str = ""
    decree: str = ""


class CourtSessionBody(BaseModel):
    sessionId: str


@router.get("/live-status")
async def live_status():
    legacy = _load_legacy_dashboard()
    return legacy.read_json(legacy.get_task_data_dir() / "live_status.json", {"tasks": [], "syncStatus": {"ok": False}})


@router.get("/agent-config")
async def agent_config():
    legacy = _load_legacy_dashboard()
    return legacy.read_json(legacy.DATA / "agent_config.json", {})


@router.get("/model-change-log")
async def model_change_log():
    legacy = _load_legacy_dashboard()
    return legacy.read_json(legacy.DATA / "model_change_log.json", [])


@router.get("/last-result")
async def last_result():
    legacy = _load_legacy_dashboard()
    return legacy.read_json(legacy.DATA / "last_model_change_result.json", {})


@router.get("/officials-stats")
async def officials_stats():
    legacy = _load_legacy_dashboard()
    return legacy.read_json(legacy.DATA / "officials_stats.json", {})


@router.get("/morning-brief")
async def morning_brief():
    legacy = _load_legacy_dashboard()
    return legacy.read_json(legacy.DATA / "morning_brief.json", {"categories": {}})


@router.get("/morning-config")
async def morning_config():
    legacy = _load_legacy_dashboard()
    return legacy.read_json(legacy.DATA / "morning_brief_config.json", {})


@router.get("/remote-skills-list")
async def remote_skills_list():
    legacy = _load_legacy_dashboard()
    return legacy.get_remote_skills_list()


@router.get("/skill-content/{agent_id}/{skill_name}")
async def skill_content(agent_id: str, skill_name: str):
    legacy = _load_legacy_dashboard()
    return legacy.read_skill_content(agent_id, skill_name)


@router.get("/task-activity/{task_id}")
async def task_activity(task_id: str):
    legacy = _load_legacy_dashboard()
    return legacy.get_task_activity(task_id)


@router.get("/scheduler-state/{task_id}")
async def scheduler_state(task_id: str):
    legacy = _load_legacy_dashboard()
    return legacy.get_scheduler_state(task_id)


@router.get("/agents-status")
async def agents_status():
    legacy = _load_legacy_dashboard()
    return legacy.get_agents_status()


@router.post("/set-model")
async def set_model(body: SetModelBody):
    legacy = _load_legacy_dashboard()
    if not body.agentId.strip() or not body.model.strip():
        raise HTTPException(status_code=400, detail="agentId and model required")

    pending_path = legacy.DATA / "pending_model_changes.json"

    def update_pending(current):
        current = [x for x in current if x.get("agentId") != body.agentId]
        current.append({"agentId": body.agentId, "model": body.model})
        return current

    legacy.atomic_json_update(pending_path, update_pending, [])

    def apply_async():
        try:
            subprocess.run(["python3", str(legacy.SCRIPTS / "apply_model_changes.py")], timeout=30)
            subprocess.run(["python3", str(legacy.SCRIPTS / "sync_agent_config.py")], timeout=10)
        except Exception:
            pass

    threading.Thread(target=apply_async, daemon=True).start()
    return {"ok": True, "message": f"Queued: {body.agentId} → {body.model}"}


@router.post("/set-dispatch-channel")
async def set_dispatch_channel(body: DispatchChannelBody):
    legacy = _load_legacy_dashboard()
    channel = body.channel.strip()
    allowed = {"feishu", "telegram", "wecom", "signal", "tui", "discord", "slack"}
    if not channel or channel not in allowed:
        raise HTTPException(status_code=400, detail=f"channel must be one of: {', '.join(sorted(allowed))}")

    def _set_channel(cfg):
        cfg["dispatchChannel"] = channel
        return cfg

    legacy.atomic_json_update(legacy.DATA / "agent_config.json", _set_channel, {})
    return {"ok": True, "message": f"派发渠道已切换为 {channel}"}


@router.post("/agent-wake")
async def agent_wake(body: AgentWakeBody):
    legacy = _load_legacy_dashboard()
    if not body.agentId.strip():
        raise HTTPException(status_code=400, detail="agentId required")
    return legacy.wake_agent(body.agentId, body.message)


@router.post("/task-action")
async def task_action(body: TaskActionBody):
    legacy = _load_legacy_dashboard()
    action = body.action.strip()
    if not body.taskId.strip() or action not in ("stop", "cancel", "resume"):
        raise HTTPException(status_code=400, detail="taskId and action(stop/cancel/resume) required")
    reason = body.reason.strip() or f"皇上从看板{action}"
    return legacy.handle_task_action(body.taskId, action, reason)


@router.post("/review-action")
async def review_action(body: ReviewActionBody):
    legacy = _load_legacy_dashboard()
    action = body.action.strip()
    if not body.taskId.strip() or action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="taskId and action(approve/reject) required")
    return legacy.handle_review_action(body.taskId, action, body.comment.strip())


@router.post("/advance-state")
async def advance_state(body: AdvanceStateBody):
    legacy = _load_legacy_dashboard()
    if not body.taskId.strip():
        raise HTTPException(status_code=400, detail="taskId required")
    return legacy.handle_advance_state(body.taskId, body.comment.strip())


@router.post("/archive-task")
async def archive_task(body: ArchiveTaskBody):
    legacy = _load_legacy_dashboard()
    task_id = (body.taskId or "").strip()
    if not task_id and not body.archiveAllDone:
        raise HTTPException(status_code=400, detail="taskId or archiveAllDone required")
    return legacy.handle_archive_task(task_id, body.archived, body.archiveAllDone)


@router.post("/scheduler-scan")
async def scheduler_scan(body: SchedulerScanBody):
    legacy = _load_legacy_dashboard()
    return legacy.handle_scheduler_scan(body.thresholdSec)


@router.post("/scheduler-retry")
async def scheduler_retry(body: SchedulerActionBody):
    legacy = _load_legacy_dashboard()
    if not body.taskId.strip():
        raise HTTPException(status_code=400, detail="taskId required")
    return legacy.handle_scheduler_retry(body.taskId, body.reason.strip())


@router.post("/scheduler-escalate")
async def scheduler_escalate(body: SchedulerActionBody):
    legacy = _load_legacy_dashboard()
    if not body.taskId.strip():
        raise HTTPException(status_code=400, detail="taskId required")
    return legacy.handle_scheduler_escalate(body.taskId, body.reason.strip())


@router.post("/scheduler-rollback")
async def scheduler_rollback(body: SchedulerActionBody):
    legacy = _load_legacy_dashboard()
    if not body.taskId.strip():
        raise HTTPException(status_code=400, detail="taskId required")
    return legacy.handle_scheduler_rollback(body.taskId, body.reason.strip())


@router.post("/morning-brief/refresh")
async def refresh_morning():
    legacy = _load_legacy_dashboard()

    def do_refresh():
        try:
            subprocess.run(["python3", str(legacy.SCRIPTS / "fetch_morning_news.py"), "--force"], timeout=120)
            legacy.push_to_feishu()
        except Exception:
            pass

    threading.Thread(target=do_refresh, daemon=True).start()
    return {"ok": True, "message": "采集已触发，约30-60秒后刷新"}


@router.post("/morning-config")
async def save_morning_config(body: dict):
    legacy = _load_legacy_dashboard()
    cfg_path = legacy.DATA / "morning_brief_config.json"
    cfg_path.write_text(legacy.json.dumps(body, ensure_ascii=False, indent=2))
    return {"ok": True, "message": "订阅配置已保存"}


@router.post("/add-skill")
async def add_skill(body: AddSkillBody):
    legacy = _load_legacy_dashboard()
    if not body.agentId.strip() or not body.skillName.strip():
        raise HTTPException(status_code=400, detail="agentId and skillName required")
    desc = body.description.strip() or body.skillName
    return legacy.add_skill_to_agent(body.agentId, body.skillName, desc, body.trigger.strip())


@router.post("/add-remote-skill")
async def add_remote_skill(body: AddRemoteSkillBody):
    legacy = _load_legacy_dashboard()
    if not body.agentId.strip() or not body.skillName.strip() or not body.sourceUrl.strip():
        raise HTTPException(status_code=400, detail="agentId, skillName, and sourceUrl required")
    return legacy.add_remote_skill(body.agentId, body.skillName, body.sourceUrl, body.description.strip())


@router.post("/update-remote-skill")
async def update_remote_skill(body: RemoteSkillBody):
    legacy = _load_legacy_dashboard()
    if not body.agentId.strip() or not body.skillName.strip():
        raise HTTPException(status_code=400, detail="agentId and skillName required")
    return legacy.update_remote_skill(body.agentId, body.skillName)


@router.post("/remove-remote-skill")
async def remove_remote_skill(body: RemoteSkillBody):
    legacy = _load_legacy_dashboard()
    if not body.agentId.strip() or not body.skillName.strip():
        raise HTTPException(status_code=400, detail="agentId and skillName required")
    return legacy.remove_remote_skill(body.agentId, body.skillName)


@router.post("/create-task")
async def create_task(body: CreateTaskBody):
    legacy = _load_legacy_dashboard()
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title required")
    return legacy.handle_create_task(
        title,
        body.org.strip(),
        body.official.strip(),
        body.priority.strip(),
        body.templateId,
        body.params or {},
        body.targetDept.strip(),
    )


@router.post("/court-discuss/start")
async def court_discuss_start(body: CourtStartBody):
    legacy = _load_legacy_dashboard()
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic required")
    officials = [o for o in body.officials if o in set(legacy.CD_PROFILES.keys())]
    if len(officials) < 2:
        raise HTTPException(status_code=400, detail="至少选择2位官员")
    return legacy.cd_create(topic, officials, body.taskId.strip())


@router.post("/court-discuss/advance")
async def court_discuss_advance(body: CourtAdvanceBody):
    legacy = _load_legacy_dashboard()
    sid = body.sessionId.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="sessionId required")
    user_msg = body.userMessage.strip() or None
    decree = body.decree.strip() or None
    return legacy.cd_advance(sid, user_msg, decree)


@router.post("/court-discuss/conclude")
async def court_discuss_conclude(body: CourtSessionBody):
    legacy = _load_legacy_dashboard()
    sid = body.sessionId.strip()
    if not sid:
        raise HTTPException(status_code=400, detail="sessionId required")
    return legacy.cd_conclude(sid)


@router.post("/court-discuss/destroy")
async def court_discuss_destroy(body: CourtSessionBody):
    legacy = _load_legacy_dashboard()
    sid = body.sessionId.strip()
    if sid:
        legacy.cd_destroy(sid)
    return {"ok": True}


@router.get("/court-discuss/fate")
async def court_discuss_fate():
    legacy = _load_legacy_dashboard()
    return {"ok": True, "event": legacy.cd_fate()}
