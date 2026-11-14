"""Durable background orchestration for physical project organization."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from easel.project_organization_models import ProjectOrganizationJob, ProjectPlan
from easel.project_organization_incremental import record_current_hashes, reusable_decisions
from easel.project_organization_plan import (
    ProjectOrganizationError,
    build_moves,
    inventory_project,
    parse_project_plan,
    project_organization_prompt,
)
from easel.project_organization_transaction import (
    apply_project_plan,
    recover_interrupted,
    rollback_transaction,
)

AgentRunner = Callable[[str, str, str], str]


class ProjectOrganizer:
    """Own one project-organization job per persona with crash-safe transactions."""

    def __init__(self, outputs: Path, runner: AgentRunner) -> None:
        self.outputs = outputs
        self.runner = runner
        self.directory = outputs / "_organize" / "jobs"
        self.lock = threading.RLock()
        self.active: set[str] = set()

    def _path(self, profile: str) -> Path:
        name = hashlib.sha256(profile.encode()).hexdigest()[:24]
        return self.directory / f"{name}.json"

    def _save(self, job: ProjectOrganizationJob) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(job.profile)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(job.model_dump_json(), encoding="utf-8")
        os.replace(temporary, path)

    def _owned(self, path: Path, profile: str) -> bool:
        try:
            value = json.loads((path / ".easel.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(value, dict) and value.get("profile") == profile

    def _projects(self, profile: str, project: str) -> tuple[str, ...]:
        if project:
            candidate = self.outputs / project
            if (not candidate.is_dir() or candidate.name.startswith(("_", "."))
                    or not self._owned(candidate, profile)):
                raise ProjectOrganizationError("project_not_found", 404)
            return (project,)
        return tuple(sorted(
            path.name for path in self.outputs.iterdir()
            if path.is_dir() and not path.name.startswith(("_", "."))
            and self._owned(path, profile)
        ))

    def current(self, profile: str) -> ProjectOrganizationJob | None:
        with self.lock:
            recover_interrupted(self.outputs)
            try:
                job = ProjectOrganizationJob.model_validate_json(
                    self._path(profile).read_text(encoding="utf-8")
                )
            except FileNotFoundError:
                return None
            except (ValidationError, OSError) as error:
                raise ProjectOrganizationError("project_organization_job_invalid", 500) from error
            alive = job.id in self.active
            if job.owner_pid and job.owner_pid != os.getpid():
                try:
                    os.kill(job.owner_pid, 0)
                    alive = True
                except ProcessLookupError:
                    alive = False
                except PermissionError:
                    alive = True
            if job.status == "running" and not alive:
                job = job.model_copy(update={
                    "status": "failed", "updated": time.time(),
                    "error": "服务已重启，整理事务已回滚，请重试",
                })
                self._save(job)
            return job

    def start(self, profile: str, project: str = "") -> ProjectOrganizationJob:
        with self.lock:
            previous = self.current(profile)
            if previous and previous.status in {"queued", "running"}:
                return previous
            if (not project and previous and previous.status == "failed"
                    and previous.rollback_available and previous.completed < previous.total
                    and len(previous.projects) == previous.total):
                resumed = previous.model_copy(update={
                    "status": "running", "error": "", "updated": time.time(),
                    "owner_pid": os.getpid(), "resume_from": previous.completed,
                    "current_project": previous.projects[previous.completed],
                })
                self.active.add(resumed.id)
                self._save(resumed)
                return resumed
            projects = self._projects(profile, project)
            carried_transactions = (
                previous.transactions
                if (not project and previous and previous.status == "done"
                    and previous.rollback_available)
                else ()
            )
            job = ProjectOrganizationJob(
                id=uuid.uuid4().hex, profile=profile, projects=projects,
                status="running" if projects else "done", total=len(projects),
                transactions=carried_transactions,
                rollback_available=bool(carried_transactions),
                updated=time.time(), owner_pid=os.getpid(),
            )
            if projects:
                self.active.add(job.id)
            self._save(job)
            return job

    def run(self, job: ProjectOrganizationJob) -> None:
        try:
            moved = job.moved
            reused_count = job.reused
            transactions = list(job.transactions)
            for index, name in enumerate(job.projects[job.resume_from:], start=job.resume_from):
                project = (self.outputs / name).resolve()
                if project.parent != self.outputs.resolve():
                    raise ProjectOrganizationError("project_path_invalid", 400)
                files = inventory_project(project)
                if files:
                    reused = reusable_decisions(project, files)
                    reused_count += len(reused)
                    known = {decision.file for decision in reused}
                    pending = [item for item in files if item.path not in known]
                    if pending:
                        response = self.runner(
                            project_organization_prompt(project, job.profile, pending),
                            job.profile,
                            f"project-organization-{job.id}-{index}",
                        )
                        plan = parse_project_plan(response, pending)
                        full_plan = ProjectPlan(items=(*reused, *plan.items))
                        moves = tuple(build_moves(project, files, full_plan))
                        transactions.append(apply_project_plan(self.outputs, project, job.profile, moves))
                        moved += sum(move.source != move.target for move in moves)
                    else:
                        record_current_hashes(project, files, reused)
                job = job.model_copy(update={
                    "completed": index + 1, "moved": moved, "current_project": name,
                    "reused": reused_count,
                    "transactions": tuple(transactions), "rollback_available": bool(transactions),
                    "updated": time.time(),
                })
                with self.lock:
                    self._save(job)
            job = job.model_copy(update={
                "status": "done", "current_project": "", "updated": time.time(),
                "rollback_available": bool(transactions), "transactions": tuple(transactions),
            })
        except ProjectOrganizationError as error:
            job = job.model_copy(update={
                "status": "failed", "error": error.code, "updated": time.time(),
            })
        except (OSError, ValueError, ValidationError) as error:
            job = job.model_copy(update={
                "status": "failed", "error": str(error)[:500], "updated": time.time(),
            })
        except Exception as error:  # noqa: BROAD_EXCEPT_OK — background task boundary.
            job = job.model_copy(update={
                "status": "failed", "updated": time.time(),
                "error": f"项目整理失败：{str(error)[:400]}",
            })
        finally:
            with self.lock:
                self.active.discard(job.id)
                self._save(job)

    def rollback(self, profile: str, identifier: str) -> ProjectOrganizationJob:
        with self.lock:
            job = self.current(profile)
            if job is None or job.id != identifier or not job.rollback_available:
                raise ProjectOrganizationError("project_rollback_unavailable", 404)
            for transaction in reversed(job.transactions):
                rollback_transaction(self.outputs, transaction)
            restored = job.model_copy(update={
                "status": "rolled_back", "rollback_available": False,
                "updated": time.time(), "error": "",
            })
            self._save(restored)
            return restored
