"""FastAPI/Jinja product workspace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agentic_company.console.support import (
    codex_execution_running,
    create_console_run,
    delivery_overview_for_run,
    execution_completed,
    read_env_keys,
    read_events,
    repo_root,
    request_codex_execution_stop,
    start_codex_execution,
    write_target_env,
)
from agentic_company.console.web.auth import decrypt_secret
from agentic_company.console.web.db import ConsoleRepository, Project, Run, User
from agentic_company.console.web.product import (
    AGENT_MODEL_OPTIONS,
    BOARD_COLUMNS,
    CODEX_MODEL_OPTIONS,
    CODEX_SERVICE_TIERS,
    REASONING_OPTIONS,
    activity_groups_for_run,
    agent_catalog,
    artifact_owner_groups,
    artifact_payload,
    artifact_phase_groups,
    artifacts_for_run,
    board_cards_for_run,
    format_request_text_with_llm,
    html_report_document,
    project_type_label,
    render_markdown,
    rendered_log_entries_for_run,
    run_timing_for_run,
    scope_size_label,
    sprint_board_groups_for_run,
    sprint_groups_for_run,
    status_label,
    system_checks,
    task_detail_for_run,
    task_report_groups_for_run,
    user_facing_blockers,
    user_friendly_artifact_label,
    work_plan_groups_for_run,
)
from agentic_company.platform.logging import configure_logging

COOKIE_NAME = "agentic_console_session"

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["status_label"] = status_label
templates.env.filters["project_type_label"] = project_type_label
templates.env.filters["scope_size_label"] = scope_size_label


def create_app(repository: ConsoleRepository | None = None) -> FastAPI:
    repo = repository or ConsoleRepository()
    repo.init_schema()
    repo.seed_public_demo_from_env()

    app = FastAPI(title="Agentic Company")
    app.state.repo = repo
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        user = optional_user(request)
        if user:
            return redirect("/dashboard")
        return redirect("/login")

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        return render(request, "login.html", user=None, error="", mode="login")

    @app.post("/login")
    def login(
        request: Request,
        identifier: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> Response:
        user = get_repo(request).authenticate_user(identifier, password)
        if not user:
            return render(
                request,
                "login.html",
                user=None,
                error="Email, username, or password did not match.",
                mode="login",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        response = redirect("/dashboard")
        set_session_cookie(response, get_repo(request).create_session(user.id))
        return response

    @app.get("/register", response_class=HTMLResponse)
    def register_page(request: Request) -> HTMLResponse:
        return render(request, "login.html", user=None, error="", mode="register")

    @app.post("/register")
    def register(
        request: Request,
        email: Annotated[str, Form()],
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> Response:
        if len(password) < 8:
            return render(
                request,
                "login.html",
                user=None,
                error="Use at least 8 characters.",
                mode="register",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            user = get_repo(request).create_user(email=email, username=username, password=password)
        except Exception:
            return render(
                request,
                "login.html",
                user=None,
                error="That email or username is already registered.",
                mode="register",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        response = redirect("/dashboard")
        set_session_cookie(response, get_repo(request).create_session(user.id))
        return response

    @app.get("/forgot-password", response_class=HTMLResponse)
    def forgot_password(request: Request) -> HTMLResponse:
        support = os.getenv("ADMIN_SUPPORT_EMAIL", "aliutov@kse.org.ua")
        return render(request, "forgot_password.html", user=None, support=support)

    @app.post("/logout")
    def logout(request: Request) -> Response:
        get_repo(request).delete_session(request.cookies.get(COOKIE_NAME))
        response = redirect("/login")
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request, user: CurrentUser) -> HTMLResponse:
        repo = get_repo(request)
        return render(
            request,
            "dashboard.html",
            user=user,
            projects=repo.list_recent_projects_for_user(user.id),
            public_demo=repo.public_demo_project(),
            checks=system_checks(openai_key_configured=bool(user_openai_key(repo, user))),
        )

    @app.get("/projects", response_class=HTMLResponse)
    def projects(request: Request, user: CurrentUser) -> HTMLResponse:
        return render(
            request,
            "projects.html",
            user=user,
            projects=get_repo(request).list_projects_for_user(user.id),
        )

    @app.get("/projects/new", response_class=HTMLResponse)
    def new_project_page(request: Request, user: CurrentUser) -> HTMLResponse:
        return render_new_project(request, user)

    @app.post("/projects")
    def create_project(
        request: Request,
        user: CurrentUser,
        name: Annotated[str, Form()] = "",
        request_text: Annotated[str, Form()] = "",
        mode: Annotated[str, Form()] = "simple_prototype",
        complexity: Annotated[str, Form()] = "simple",
        reasoning: Annotated[str, Form()] = "medium",
        agent_model: Annotated[str, Form()] = "gpt-4.1",
        codex_model: Annotated[str, Form()] = "gpt-5.3-codex",
        codex_reasoning: Annotated[str, Form()] = "medium",
        service_tier: Annotated[str, Form()] = "standard",
    ) -> Response:
        if not name.strip() or not request_text.strip():
            return render_new_project(
                request,
                user,
                error="Project name and request are required.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        repo = get_repo(request)
        api_key = user_openai_key(repo, user)
        if not api_key:
            return render_new_project(
                request,
                user,
                error=(
                    "Add your OpenAI key in Settings before starting. "
                    "It powers your private projects."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        project = repo.create_project(
            owner_user_id=user.id,
            name=name,
            request_text=request_text,
            mode=mode,
            complexity=complexity,
            status="starting",
        )
        run_dir = create_web_console_run(
            user.username,
            _run_requirements(name, request_text, mode, complexity),
        )
        prepare_run_environment(
            run_dir,
            api_key=api_key,
            agent_model=agent_model,
            agent_reasoning=reasoning,
            codex_model=codex_model,
            codex_reasoning=codex_reasoning,
            service_tier=service_tier,
        )
        run = repo.create_run(
            project_id=project.id,
            run_uid=run_dir.name,
            run_dir=run_dir,
            status="running",
            mode=mode,
            reasoning=reasoning,
        )
        try:
            start_codex_execution(run_dir)
        except Exception:
            repo.update_run_status(run.id, "failed_to_start")
            repo.update_project_status(project.id, "failed_to_start")
            raise
        repo.update_project_status(project.id, "running")
        return redirect(f"/projects/{project.id}")

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_workspace(
        request: Request,
        project_id: int,
        user: CurrentUser,
        tab: str = "overview",
    ) -> HTMLResponse:
        project = get_visible_project_or_404(request, project_id, user)
        runs = get_repo(request).runs_for_project(project.id, user.id)
        run = runs[0] if runs else None
        return render_workspace(request, user, project, run, runs, tab)

    @app.post("/projects/{project_id}/restart")
    def restart_project(
        request: Request,
        project_id: int,
        user: CurrentUser,
    ) -> Response:
        repo = get_repo(request)
        project = repo.get_project_for_user(project_id, user.id)
        if not project or project.visibility == "public_demo":
            raise HTTPException(status_code=403)
        request_text = repo.project_request_text(project.id, user.id)
        if not request_text.strip():
            raise HTTPException(status_code=400)
        api_key = user_openai_key(repo, user)
        if not api_key:
            return redirect("/settings")
        settings = restart_run_settings(repo, project, user)
        run_dir = create_web_console_run(
            user.username,
            _run_requirements(project.name, request_text, project.mode, project.complexity),
        )
        prepare_run_environment(
            run_dir,
            api_key=api_key,
            agent_model=settings["agent_model"],
            agent_reasoning=settings["agent_reasoning"],
            codex_model=settings["codex_model"],
            codex_reasoning=settings["codex_reasoning"],
            service_tier=settings["service_tier"],
        )
        run = repo.create_run(
            project_id=project.id,
            run_uid=run_dir.name,
            run_dir=run_dir,
            status="running",
            mode=project.mode,
            reasoning=settings["agent_reasoning"],
        )
        try:
            start_codex_execution(run_dir)
        except Exception:
            repo.update_run_status(run.id, "failed_to_start")
            repo.update_project_status(project.id, "failed_to_start")
            raise
        repo.update_project_status(project.id, "running")
        return redirect(f"/projects/{project.id}")

    @app.post("/projects/{project_id}/stop")
    def stop_project(
        request: Request,
        project_id: int,
        user: CurrentUser,
    ) -> Response:
        repo = get_repo(request)
        project = repo.get_project_for_user(project_id, user.id)
        if not project or project.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        run = repo.latest_run_for_project(project.id, user.id)
        if run:
            request_codex_execution_stop(Path(run.run_dir))
            repo.update_run_status(run.id, "stopped")
        repo.update_project_status(project.id, "stopped")
        return redirect(f"/projects/{project.id}")

    @app.post("/projects/{project_id}/promote")
    def promote_project(
        request: Request,
        project_id: int,
        user: CurrentUser,
    ) -> Response:
        repo = get_repo(request)
        project = repo.get_project_for_user(project_id, user.id)
        if not project or project.owner_user_id != user.id:
            raise HTTPException(status_code=404)
        latest_run = repo.latest_run_for_project(project.id, user.id)
        if not _run_showcase_ready(latest_run):
            raise HTTPException(status_code=400, detail="Project is not ready for showcase yet.")
        promoted = repo.set_project_visibility(project_id, user.id, "public_demo")
        if not promoted:
            raise HTTPException(status_code=404)
        return redirect(f"/projects/{project_id}")

    @app.post("/projects/{project_id}/demote")
    def demote_project(
        request: Request,
        project_id: int,
        user: CurrentUser,
    ) -> Response:
        demoted = get_repo(request).set_project_visibility(project_id, user.id, "private")
        if not demoted:
            raise HTTPException(status_code=404)
        return redirect(f"/projects/{project_id}")

    @app.post("/projects/{project_id}/delete")
    def delete_project(
        request: Request,
        project_id: int,
        user: CurrentUser,
    ) -> Response:
        deleted = get_repo(request).delete_private_project(project_id, user.id)
        if not deleted:
            raise HTTPException(status_code=404)
        return redirect("/projects")

    @app.get("/public-demo", response_class=HTMLResponse)
    def public_demo(request: Request, user: CurrentUser) -> Response:
        project = get_repo(request).public_demo_project()
        if project:
            return redirect(f"/projects/{project.id}")
        return render(request, "public_demo_empty.html", user=user)

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, user: CurrentUser) -> HTMLResponse:
        credential = get_repo(request).get_provider_secret(user.id, "openai")
        return render(
            request,
            "settings.html",
            user=user,
            credential=credential,
            checks=system_checks(
                openai_key_configured=bool(user_openai_key(get_repo(request), user))
            ),
            agents=agent_catalog(),
            agent_models=AGENT_MODEL_OPTIONS,
            codex_models=CODEX_MODEL_OPTIONS,
            reasoning_options=REASONING_OPTIONS,
            service_tiers=CODEX_SERVICE_TIERS,
        )

    @app.post("/settings/openai")
    def save_openai_key(
        request: Request,
        user: CurrentUser,
        api_key: Annotated[str, Form()] = "",
    ) -> Response:
        if api_key.strip():
            get_repo(request).save_provider_secret(user.id, "openai", api_key.strip())
        return redirect("/settings")

    @app.post("/settings/openai/delete")
    def delete_openai_key(request: Request, user: CurrentUser) -> Response:
        get_repo(request).delete_provider_secret(user.id, "openai")
        return redirect("/settings")

    @app.get("/agents", response_class=HTMLResponse)
    def agents_page(request: Request, user: CurrentUser) -> HTMLResponse:
        return render(
            request,
            "agents.html",
            user=user,
            agents=agent_catalog(),
            agent_models=AGENT_MODEL_OPTIONS,
            codex_models=CODEX_MODEL_OPTIONS,
            reasoning_options=REASONING_OPTIONS,
        )

    @app.get("/artifacts", response_class=HTMLResponse)
    def artifacts_index(request: Request, user: CurrentUser) -> HTMLResponse:
        projects = get_repo(request).list_projects_for_user(user.id)
        public_demo = get_repo(request).public_demo_project()
        return render(
            request,
            "artifacts_index.html",
            user=user,
            projects=projects,
            public_demo=public_demo,
        )

    @app.get("/system-check", response_class=HTMLResponse)
    def system_check(request: Request, user: CurrentUser) -> HTMLResponse:
        return render(
            request,
            "system_check.html",
            user=user,
            checks=system_checks(
                openai_key_configured=bool(user_openai_key(get_repo(request), user))
            ),
        )

    @app.get("/artifacts/{run_id}/{artifact_path:path}", response_class=HTMLResponse)
    def artifact_view(
        request: Request,
        run_id: int,
        artifact_path: str,
        user: CurrentUser,
        raw: bool = False,
    ) -> HTMLResponse:
        run = get_repo(request).get_run_for_user(run_id, user.id)
        if not run:
            raise HTTPException(status_code=404)
        try:
            payload = artifact_payload(Path(run.run_dir), artifact_path)
        except (OSError, ValueError):
            raise HTTPException(status_code=404) from None
        if raw and payload["kind"] == "html":
            return HTMLResponse(html_report_document(str(payload["content"])))
        return render(
            request,
            "artifact_view.html",
            user=user,
            run=run,
            artifact_path=artifact_path,
            artifact_title=user_friendly_artifact_label(Path(artifact_path).name, artifact_path),
            payload=payload,
        )

    @app.get("/projects/{project_id}/tasks/{task_id}", response_class=HTMLResponse)
    def task_detail(
        request: Request,
        project_id: int,
        task_id: str,
        user: CurrentUser,
    ) -> HTMLResponse:
        project = get_visible_project_or_404(request, project_id, user)
        run = get_repo(request).latest_run_for_project(project.id, user.id)
        if not run:
            raise HTTPException(status_code=404)
        run_dir = Path(run.run_dir)
        artifacts = artifacts_for_run(run_dir)[0]
        detail = task_detail_for_run(run_dir, task_id, artifacts)
        if detail is None:
            raise HTTPException(status_code=404)
        return render(
            request,
            "task_detail.html",
            user=user,
            project=project,
            run=run,
            detail=detail,
        )

    @app.get("/api/runs/{run_id}/status")
    def run_status(
        request: Request,
        run_id: int,
        user: CurrentUser,
    ) -> JSONResponse:
        run = get_repo(request).get_run_for_user(run_id, user.id)
        if not run:
            raise HTTPException(status_code=404)
        run_dir = Path(run.run_dir)
        overview = delivery_overview_for_run(run_dir)
        if run.status == "stopped":
            running = False
            completed = False
            status_value = "stopped"
        else:
            running = codex_execution_running(run_dir)
            completed = execution_completed(run_dir)
            status_value = "running" if running else overview.status
            if completed and not running:
                status_value = overview.status or "completed"
            get_repo(request).update_run_status(run.id, status_value)
        return JSONResponse(
            {
                "status": status_label(status_value),
                "stage": status_label(overview.stage),
                "running": running,
                "completed": completed,
                "events": len(read_events(run_dir)),
            }
        )

    @app.get("/api/runs/{run_id}/logs")
    def run_logs(
        request: Request,
        run_id: int,
        user: CurrentUser,
    ) -> JSONResponse:
        run = get_repo(request).get_run_for_user(run_id, user.id)
        if not run:
            raise HTTPException(status_code=404)
        run_dir = Path(run.run_dir)
        return JSONResponse(
            {
                "logs": rendered_log_entries_for_run(run_dir),
                "groups": activity_groups_for_run(run_dir),
            }
        )

    @app.post("/api/format-request")
    async def format_request(request: Request, user: CurrentUser) -> JSONResponse:
        form = await request.form()
        text = str(form.get("text", ""))
        api_key = user_openai_key(get_repo(request), user)
        return JSONResponse({"formatted": format_request_text_with_llm(text, api_key=api_key)})

    return app


def get_repo(request: Request) -> ConsoleRepository:
    return request.app.state.repo


def optional_user(request: Request) -> User | None:
    return get_repo(request).user_for_session(request.cookies.get(COOKIE_NAME))


def require_user(request: Request) -> User:
    user = optional_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user


CurrentUser = Annotated[User, Depends(require_user)]


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=os.getenv("AGENTIC_COOKIE_SECURE", "").lower() == "true",
        samesite="lax",
        max_age=14 * 24 * 60 * 60,
    )


def get_visible_project_or_404(request: Request, project_id: int, user: User) -> Project:
    project = get_repo(request).get_project_for_user(project_id, user.id)
    if project is None:
        raise HTTPException(status_code=404)
    return project


def _run_showcase_ready(run: Run | None) -> bool:
    if run is None or run.status == "stopped":
        return False
    run_dir = Path(run.run_dir)
    if codex_execution_running(run_dir) or not execution_completed(run_dir):
        return False
    overview = delivery_overview_for_run(run_dir)
    status_token = f"{overview.status} {overview.stage}".lower()
    if any(token in status_token for token in ("blocked", "failed", "stopped")):
        return False
    return not overview.blockers


def render_workspace(
    request: Request,
    user: User,
    project: Project,
    run: Run | None,
    runs: list[Run],
    tab: str,
) -> HTMLResponse:
    context = {
        "user": user,
        "project": project,
        "runs": runs,
        "run": run,
        "tab": tab,
        "board_columns": BOARD_COLUMNS,
        "board": {},
        "sprints": {},
        "work_plan_groups": [],
        "business_artifacts": [],
        "business_artifact_groups": [],
        "business_artifact_phase_groups": [],
        "task_report_groups": [],
        "technical_artifacts": [],
        "logs": [],
        "activity_groups": [],
        "agents": agent_catalog(),
        "overview": None,
        "overview_blockers": [],
        "project_request_html": render_markdown(_project_request_text(project, run)),
        "sprint_board_groups": [],
        "run_running": False,
        "run_completed": False,
        "can_restart": False,
        "showcase_ready": False,
        "run_timing": {},
    }
    if run:
        run_dir = Path(run.run_dir)
        run_running = codex_execution_running(run_dir)
        run_completed = execution_completed(run_dir)
        overview = delivery_overview_for_run(run_dir)
        business_artifacts = artifacts_for_run(run_dir)[0]
        context.update(
            {
                "overview": overview,
                "overview_blockers": user_facing_blockers(overview.blockers),
                "board": board_cards_for_run(run_dir),
                "sprints": sprint_groups_for_run(run_dir),
                "work_plan_groups": work_plan_groups_for_run(run_dir),
                "sprint_board_groups": sprint_board_groups_for_run(run_dir),
                "business_artifacts": business_artifacts,
                "business_artifact_groups": artifact_owner_groups(business_artifacts),
                "business_artifact_phase_groups": artifact_phase_groups(business_artifacts),
                "task_report_groups": task_report_groups_for_run(run_dir, business_artifacts),
                "technical_artifacts": [],
                "logs": rendered_log_entries_for_run(run_dir),
                "activity_groups": activity_groups_for_run(run_dir),
                "run_running": run_running,
                "run_completed": run_completed,
                "can_restart": (
                    project.owner_user_id == user.id
                    and project.visibility != "public_demo"
                    and not run_running
                ),
                "showcase_ready": _run_showcase_ready(run),
                "run_timing": run_timing_for_run(run_dir),
            }
        )
    return render(request, "project_workspace.html", **context)


def _project_request_text(project: Project, run: Run | None) -> str:
    if project.request_text.strip():
        return project.request_text
    if run is None:
        return ""
    requirements_path = Path(run.run_dir) / "00-requirements.md"
    try:
        return requirements_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def render(
    request: Request,
    template_name: str,
    status_code: int = 200,
    **context: object,
) -> HTMLResponse:
    context.setdefault("request", request)
    context.setdefault("root", repo_root())
    context.setdefault("asset_version", static_asset_version())
    return templates.TemplateResponse(request, template_name, context, status_code=status_code)


def static_asset_version() -> str:
    static_files = (
        STATIC_DIR / "styles.css",
        STATIC_DIR / "app.js",
        STATIC_DIR / "favicon.svg",
    )
    newest = max(
        (path.stat().st_mtime_ns for path in static_files if path.exists()),
        default=0,
    )
    return str(newest)


def render_new_project(
    request: Request,
    user: User,
    *,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "new_project.html",
        user=user,
        formatted="",
        error=error,
        credential=get_repo(request).get_provider_secret(user.id, "openai"),
        agent_models=AGENT_MODEL_OPTIONS,
        codex_models=CODEX_MODEL_OPTIONS,
        reasoning_options=REASONING_OPTIONS,
        service_tiers=CODEX_SERVICE_TIERS,
        status_code=status_code,
    )


def user_openai_key(repo: ConsoleRepository, user: User) -> str:
    credential = repo.get_provider_secret(user.id, "openai")
    if credential is None:
        return ""
    if credential.storage_mode == "encrypted":
        return decrypt_secret(credential.encrypted_value)
    if credential.storage_mode == "local_demo":
        return credential.encrypted_value
    return ""


def prepare_run_environment(
    run_dir: Path,
    *,
    api_key: str,
    agent_model: str,
    agent_reasoning: str,
    codex_model: str,
    codex_reasoning: str,
    service_tier: str,
) -> Path:
    values = {
        "OPENAI_API_KEY": api_key,
        "CODEX_API_KEY": api_key,
        "AGENT_LLM_MODEL": agent_model,
        "COORDINATOR_AGENT_REASONING_EFFORT": agent_reasoning,
        "SPECIALIST_AGENT_REASONING_EFFORT": agent_reasoning,
        "AGENT_CODEX_MODEL": codex_model,
        "BUSINESS_ANALYST_CODEX_MODEL": codex_model,
        "ARCHITECT_CODEX_MODEL": codex_model,
        "PROJECT_MANAGER_CODEX_MODEL": codex_model,
        "FULLSTACK_CODEX_MODEL": codex_model,
        "QUALITY_CODEX_MODEL": codex_model,
        "DEPLOYMENT_CODEX_MODEL": codex_model,
        "HANDOFF_CODEX_MODEL": codex_model,
        "AGENTIC_CODEX_REASONING_EFFORT": codex_reasoning,
        "AGENTIC_CODEX_SERVICE_TIER": service_tier,
    }
    return write_target_env(run_dir, values)


def restart_run_settings(repo: ConsoleRepository, project: Project, user: User) -> dict[str, str]:
    latest_run = repo.latest_run_for_project(project.id, user.id)
    latest_env = _run_env(latest_run)
    agent_reasoning = (
        latest_env.get("COORDINATOR_AGENT_REASONING_EFFORT")
        or latest_env.get("SPECIALIST_AGENT_REASONING_EFFORT")
        or (latest_run.reasoning if latest_run else "")
        or "medium"
    )
    codex_model = (
        latest_env.get("AGENT_CODEX_MODEL")
        or latest_env.get("FULLSTACK_CODEX_MODEL")
        or latest_env.get("BUSINESS_ANALYST_CODEX_MODEL")
        or "gpt-5.3-codex"
    )
    return {
        "agent_model": latest_env.get("AGENT_LLM_MODEL", "gpt-4.1"),
        "agent_reasoning": agent_reasoning,
        "codex_model": codex_model,
        "codex_reasoning": latest_env.get("AGENTIC_CODEX_REASONING_EFFORT", "medium"),
        "service_tier": latest_env.get("AGENTIC_CODEX_SERVICE_TIER", "standard"),
    }


def _run_env(run: Run | None) -> dict[str, str]:
    if run is None:
        return {}
    return read_env_keys(Path(run.run_dir) / "generated-project" / ".env")


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)


def create_web_console_run(username: str, requirements_text: str) -> Path:
    output_root = repo_root() / "runs" / _slug(username)
    return create_console_run(
        requirements_text,
        output_root,
        run_id_prefix="project",
    )


def _slug(value: str) -> str:
    import re

    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip().lower()).strip("-")
    return slug or "user"


def _run_requirements(name: str, request_text: str, mode: str, complexity: str) -> str:
    return (
        f"# {name.strip()}\n\n"
        f"Project type: {project_type_label(mode)}\n"
        f"Scope size: {scope_size_label(complexity)}\n\n"
        f"{request_text.strip()}\n"
    )


def main() -> None:
    configure_logging()
    host = os.getenv("AGENTIC_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("AGENTIC_WEB_PORT", "8503"))
    uvicorn.run(
        "agentic_company.console.web.app:create_app",
        host=host,
        port=port,
        reload=False,
        factory=True,
    )


if __name__ == "__main__":
    main()
