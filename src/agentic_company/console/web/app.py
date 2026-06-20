"""FastAPI/Jinja product workspace."""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated, Any
from urllib.parse import urlparse

import uvicorn
from fastapi import Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from agentic_company.console.support import (
    agent_runtime_env_path,
    create_console_run,
    read_env_keys,
    repo_root,
    prepare_codex_execution_continue,
    request_codex_execution_stop,
    start_codex_execution,
    write_target_env,
)
from agentic_company.console.web import github_oauth
from agentic_company.console.web.auth import SecretEncryptionUnavailable, decrypt_secret
from agentic_company.console.web.db import ConsoleRepository, Project, Run, User
from agentic_company.console.web.product import (
    AGENT_MODEL_OPTIONS,
    AGENT_PROVIDER_OPTIONS,
    BOARD_COLUMNS,
    CODEX_MODEL_OPTIONS,
    CODEX_SERVICE_TIERS,
    GEMINI_MODEL_OPTIONS,
    REASONING_OPTIONS,
    GeminiFormatterUnavailable,
    activity_groups_from_db_events,
    agent_catalog,
    agent_icon_path,
    artifact_owner_groups,
    artifact_payload_for_record,
    artifact_phase_groups,
    board_cards_from_work_items,
    board_groups_from_work_items,
    canonical_artifacts_for_run,
    delivery_overview_from_work_items,
    format_request_text_with_llm,
    html_report_document,
    project_type_label,
    render_markdown,
    rendered_log_entries_from_db_events,
    run_timing_from_work_items,
    scope_size_label,
    sprint_board_groups_from_work_items,
    status_label,
    system_checks,
    task_detail_from_work_items,
    task_report_groups_from_work_items,
    user_facing_blockers,
    work_plan_groups_from_work_items,
)
from agentic_company.console.web.rate_limit import RateLimiter
from agentic_company.integrations.codex import DEFAULT_CODEX_MODEL
from agentic_company.integrations.codex import account_auth as codex_account_auth
from agentic_company.integrations.codex.runner import (
    CODEX_AUTH_MODE_ENV,
    CODEX_AUTH_MODE_USER_CHATGPT,
    codex_auth_mode_from_env,
)
from agentic_company.platform.db.runtime_cache import redis_error_types, runtime_cache_from_env
from agentic_company.platform.db.runtime_db import (
    reconcile_run,
    reconcile_stale_console_runs,
    record_run_lifecycle,
    request_run_control_intent,
)
from agentic_company.platform.logging import configure_logging
from agentic_company.platform.run.run_finalizer import RunStatus
from agentic_company.platform.run.run_trace import trace_summary

COOKIE_NAME = "agentic_console_session"
LOGGER = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["status_label"] = status_label
templates.env.filters["project_type_label"] = project_type_label
templates.env.filters["scope_size_label"] = scope_size_label
templates.env.filters["agent_icon"] = agent_icon_path


def create_app(repository: ConsoleRepository | None = None) -> FastAPI:
    repo = repository or ConsoleRepository()
    repo.init_schema()
    repo.seed_public_demo_from_env()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            results = reconcile_stale_console_runs(repo)
            if results:
                LOGGER.info("Startup reconciled stale console runs count=%s", len(results))
        except Exception:
            LOGGER.exception("Startup stale-run reconciliation failed")
        yield

    app = FastAPI(title="Agentic Company", lifespan=lifespan)
    app.state.repo = repo
    app.state.login_rate_limiter = RateLimiter(max_attempts=10, window_seconds=300)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def reject_cross_origin_writes(request: Request, call_next: Any) -> Response:
        """Block state-changing requests whose Origin is a different site.

        A present-but-mismatched Origin is a cross-site browser request; a missing
        Origin (API clients, same-origin GETs) is allowed. Defense-in-depth atop
        the SameSite=lax session cookie.
        """

        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.url.netloc:
                return JSONResponse(
                    {"detail": "Cross-origin request rejected."},
                    status_code=status.HTTP_403_FORBIDDEN,
                )
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Response:
        user = optional_user(request)
        return render(
            request,
            "landing.html",
            user=None,
            workspace_href="/dashboard" if user else "/login",
            showcase_href="/public-demo" if user else "/login",
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request) -> HTMLResponse:
        return render(request, "login.html", user=None, error="", mode="login")

    @app.post("/login")
    def login(
        request: Request,
        identifier: Annotated[str, Form()],
        password: Annotated[str, Form()],
    ) -> Response:
        limiter: RateLimiter = request.app.state.login_rate_limiter
        # Throttle per source address, not per (source, username): keying on the
        # identifier would let an attacker reset the budget by cycling usernames.
        attempt_key = _client_ip(request)
        if not limiter.allow(attempt_key):
            return render(
                request,
                "login.html",
                user=None,
                error="Too many sign-in attempts. Wait a few minutes and try again.",
                mode="login",
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
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
        limiter.reset(attempt_key)
        response = redirect("/dashboard")
        set_session_cookie(
            response, get_repo(request).create_session(user.id), secure=_cookie_secure(request)
        )
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
        set_session_cookie(
            response, get_repo(request).create_session(user.id), secure=_cookie_secure(request)
        )
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
            projects=projects_with_display_requests(
                repo, user.id, repo.list_recent_projects_for_user(user.id, limit=3)
            ),
            public_demos=projects_with_display_requests(
                repo, user.id, repo.list_public_demo_projects(limit=3)
            ),
            checks=system_checks(
                openai_key_configured=bool(user_openai_key(repo, user)),
                gemini_key_configured=bool(user_or_platform_gemini_key(repo, user)),
            ),
        )

    @app.get("/projects", response_class=HTMLResponse)
    def projects(request: Request, user: CurrentUser) -> HTMLResponse:
        return render(
            request,
            "projects.html",
            user=user,
            projects=projects_with_display_requests(
                get_repo(request), user.id, get_repo(request).list_projects_for_user(user.id)
            ),
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
        agent_provider: Annotated[str, Form()] = "google_gemini",
        agent_model: Annotated[str, Form()] = "gemini-3.1-flash-lite",
        codex_model: Annotated[str, Form()] = DEFAULT_CODEX_MODEL,
        codex_reasoning: Annotated[str, Form()] = "medium",
        service_tier: Annotated[str, Form()] = "standard",
        board_adapter: Annotated[str, Form()] = "internal",
        repository: Annotated[str, Form()] = "",
        project_owner: Annotated[str, Form()] = "",
        new_repo_name: Annotated[str, Form()] = "",
        new_repo_private: Annotated[str, Form()] = "",
    ) -> Response:
        agent_provider = normalize_agent_provider(agent_provider)
        agent_model = normalize_agent_model(agent_provider, agent_model)
        codex_model = normalize_codex_model(codex_model)
        board_adapter = normalize_board_adapter(board_adapter)
        form_values = new_project_form_values(
            name=name,
            request_text=request_text,
            mode=mode,
            complexity=complexity,
            agent_provider=agent_provider,
            agent_model=agent_model,
            codex_model=codex_model,
            codex_reasoning=codex_reasoning,
            service_tier=service_tier,
            board_adapter=board_adapter,
            repository=repository,
            project_owner=project_owner,
        )
        if not name.strip() or not request_text.strip():
            return render_new_project(
                request,
                user,
                error="Project name and request are required.",
                form_values=form_values,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        repo = get_repo(request)
        api_key = user_openai_key(repo, user)
        if agent_provider == "openai" and not api_key:
            return render_new_project(
                request,
                user,
                error=(
                    "Add your OpenAI key in Settings before starting OpenAI-backed planning."
                ),
                form_values=form_values,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        codex_error = _codex_start_preflight(repo, user)
        if codex_error:
            return render_new_project(
                request,
                user,
                error=codex_error,
                form_values=form_values,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        gemini_api_key = user_or_platform_gemini_key(repo, user)
        if agent_provider == "google_gemini" and not gemini_api_key:
            return render_new_project(
                request,
                user,
                error="Gemini is not configured yet. Add a Gemini key in Settings.",
                form_values=form_values,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        # GitHub repo: create a new one on the user's account if asked, else use the
        # picked one. Any repo selection implies the GitHub board.
        gh_token = _github_token(repo, user)
        if new_repo_name.strip() and gh_token:
            try:
                created = github_oauth.create_repo(
                    gh_token, new_repo_name.strip(), private=bool(new_repo_private.strip())
                )
                repository = created.full_name
            except github_oauth.GitHubOAuthError as exc:
                return render_new_project(
                    request,
                    user,
                    error=f"Could not create repository '{new_repo_name.strip()}': {exc}",
                    form_values=form_values,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
        if repository.strip():
            board_adapter = "github"
        project = repo.create_project(
            owner_user_id=user.id,
            name=name,
            request_text=request_text,
            mode=mode,
            complexity=complexity,
            status="starting",
        )
        _maybe_create_board_connection(
            repo,
            project_id=project.id,
            board_adapter=board_adapter,
            repository=repository,
            project_owner=project_owner,
            token_ref=f"user:{user.id}:{github_oauth.PROVIDER}" if gh_token else "",
        )
        run_dir = create_web_console_run(
            user.username,
            _run_requirements(name, request_text, mode, complexity),
        )
        prepare_run_environment(
            run_dir,
            gemini_api_key=gemini_api_key,
            agent_provider=agent_provider,
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
        if tab == "logs":
            tab = "board"
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
        settings = restart_run_settings(repo, project, user)
        codex_error = _codex_start_preflight(repo, user, ignore_project_id=project.id)
        if codex_error:
            return redirect("/settings")
        api_key = user_openai_key(repo, user)
        if settings["agent_provider"] == "openai" and not api_key:
            return redirect("/settings")
        gemini_api_key = user_or_platform_gemini_key(repo, user)
        if settings["agent_provider"] == "google_gemini" and not gemini_api_key:
            return redirect("/settings")
        run_dir = create_web_console_run(
            user.username,
            _run_requirements(project.name, request_text, project.mode, project.complexity),
        )
        gh_conn = repo.get_active_work_system_connection(project_id=project.id, system="github")
        prepare_run_environment(
            run_dir,
            gemini_api_key=gemini_api_key,
            agent_provider=settings["agent_provider"],
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

    @app.post("/projects/{project_id}/continue")
    def continue_project(
        request: Request,
        project_id: int,
        user: CurrentUser,
    ) -> Response:
        repo = get_repo(request)
        project = repo.get_project_for_user(project_id, user.id)
        if not project or project.visibility == "public_demo" or project.owner_user_id != user.id:
            raise HTTPException(status_code=403)
        run = repo.latest_run_for_project(project.id, user.id)
        if run is None:
            raise HTTPException(status_code=400, detail="Project has no run to continue.")
        if _run_status_running(run.status) or _run_delivery_done(run.status):
            return redirect(f"/projects/{project.id}")
        codex_error = _codex_start_preflight(repo, user, ignore_project_id=project.id)
        if codex_error:
            return redirect("/settings")
        run_dir = Path(run.run_dir)
        repo.clear_run_control_intent(
            run.id,
            reconcile_status="continued",
            reconcile_reason="Operator continued the same run.",
        )
        prepare_codex_execution_continue(run_dir)
        repo.update_run_status(run.id, "running")
        repo.update_project_status(project.id, "running")
        try:
            start_codex_execution(run_dir)
        except Exception:
            repo.update_run_status(run.id, "failed_to_start")
            repo.update_project_status(project.id, "failed_to_start")
            raise
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
            request_run_control_intent(run.run_uid, "cancel", "Stopped by user.")
            repo.request_console_process_stop(run.id, process_name="codex_execution")
            request_codex_execution_stop(Path(run.run_dir))
            reconcile_run(run.run_uid)
            record_run_lifecycle(run.run_uid, RunStatus.STOPPED)
            try:
                cache = runtime_cache_from_env()
            except ValueError as exc:
                LOGGER.warning(
                    "Redis runtime cache config invalid after durable stop run_uid=%s error=%s",
                    run.run_uid,
                    exc,
                )
            else:
                try:
                    cache.set_stop_requested(run.run_uid)
                except redis_error_types() as exc:
                    LOGGER.warning(
                        "Redis stop flag write failed after durable stop run_uid=%s error=%s",
                        run.run_uid,
                        exc,
                    )
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
        if not _run_showcase_ready(repo, latest_run):
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
        return render(
            request,
            "public_demo_empty.html",
            user=user,
            public_demos=projects_with_display_requests(
                get_repo(request), user.id, get_repo(request).list_public_demo_projects()
            ),
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request, user: CurrentUser, key_error: str = "") -> HTMLResponse:
        repo = get_repo(request)
        credential = repo.get_provider_secret(user.id, "openai")
        gemini_credential = repo.get_provider_secret(user.id, "google_gemini")
        return render(
            request,
            "settings.html",
            user=user,
            key_error=bool(key_error),
            credential=credential,
            gemini_credential=gemini_credential,
            github_configured=github_oauth.is_configured(),
            github_connected=bool(_github_token(repo, user)),
            github_login=_github_login(repo, user),
            codex_auth=repo.get_codex_auth_connection(user.id),
            codex_device_login=codex_account_auth.codex_device_login_state(user.id),
            codex_auth_mode=codex_auth_mode_from_env(),
            checks=system_checks(
                openai_key_configured=bool(user_openai_key(repo, user)),
                gemini_key_configured=bool(user_or_platform_gemini_key(repo, user)),
            ),
            agents=agent_catalog_for_run(_latest_user_run(repo, user.id)),
            agent_providers=AGENT_PROVIDER_OPTIONS,
            agent_models=AGENT_MODEL_OPTIONS,
            gemini_models=GEMINI_MODEL_OPTIONS,
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
            try:
                get_repo(request).save_provider_secret(user.id, "openai", api_key.strip())
            except SecretEncryptionUnavailable:
                return redirect("/settings?key_error=1")
        return redirect("/settings")

    @app.post("/settings/openai/delete")
    def delete_openai_key(request: Request, user: CurrentUser) -> Response:
        get_repo(request).delete_provider_secret(user.id, "openai")
        return redirect("/settings")

    @app.post("/settings/gemini")
    def save_gemini_key(
        request: Request,
        user: CurrentUser,
        api_key: Annotated[str, Form()] = "",
    ) -> Response:
        if api_key.strip():
            try:
                get_repo(request).save_provider_secret(user.id, "google_gemini", api_key.strip())
            except SecretEncryptionUnavailable:
                return redirect("/settings?key_error=1")
        return redirect("/settings")

    @app.post("/settings/gemini/delete")
    def delete_gemini_key(request: Request, user: CurrentUser) -> Response:
        get_repo(request).delete_provider_secret(user.id, "google_gemini")
        return redirect("/settings")

    @app.post("/auth/codex/connect")
    def codex_connect(request: Request, user: CurrentUser) -> Response:
        repo = get_repo(request)
        try:
            codex_account_auth.start_codex_device_login(user.id)
        except OSError as exc:
            repo.upsert_codex_auth_connection(
                user.id,
                auth_slot=f"user:{user.id}",
                status="error",
                last_error=str(exc),
            )
            return redirect("/settings?codex_error=start")
        repo.upsert_codex_auth_connection(
            user.id,
            auth_slot=f"user:{user.id}",
            status="pending",
            auth_mode="chatgpt",
        )
        return redirect("/settings?codex_login=started")

    @app.post("/auth/codex/check")
    def codex_check(request: Request, user: CurrentUser) -> Response:
        repo = get_repo(request)
        status_result = codex_account_auth.codex_login_status(user.id)
        repo.upsert_codex_auth_connection(
            user.id,
            auth_slot=f"user:{user.id}",
            status="connected" if status_result.connected else "needs_relogin",
            auth_mode=status_result.auth_mode or "chatgpt",
            last_error="" if status_result.connected else status_result.message,
        )
        return redirect("/settings")

    @app.post("/auth/codex/disconnect")
    def codex_disconnect(request: Request, user: CurrentUser) -> Response:
        repo = get_repo(request)
        repo.delete_codex_auth_connection(user.id)
        codex_account_auth.delete_codex_home_for_user(user.id)
        return redirect("/settings")

    @app.get("/api/codex/device-login")
    def codex_device_login_api(request: Request, user: CurrentUser) -> JSONResponse:
        return JSONResponse(codex_account_auth.codex_device_login_state(user.id))

    # ---- GitHub OAuth: "Login with GitHub" + repo picker ----
    def _github_callback_uri(request: Request) -> str:
        # MUST exactly match the OAuth App's registered Authorization callback URL.
        # Derive a stable 127.0.0.1:<port> URL (not request.base_url) so opening the
        # console as "localhost" never mismatches the registration. Override with
        # GITHUB_OAUTH_CALLBACK_URL when the console is hosted elsewhere.
        configured = (os.environ.get("GITHUB_OAUTH_CALLBACK_URL") or "").strip()
        if configured:
            return configured
        port = (os.environ.get("AGENTIC_WEB_PORT") or "8503").strip() or "8503"
        return f"http://127.0.0.1:{port}/auth/github/callback"

    @app.get("/auth/github/login")
    def github_login(request: Request, user: CurrentUser, next: str = "/projects/new") -> Response:
        if not github_oauth.is_configured():
            return redirect("/settings?github_error=not_configured")
        state = secrets.token_urlsafe(24)
        url = github_oauth.build_authorize_url(_github_callback_uri(request), state)
        resp = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
        resp.set_cookie("gh_oauth_state", state, max_age=600, httponly=True, samesite="lax")
        resp.set_cookie(
            "gh_oauth_next",
            next if next.startswith("/") else "/projects/new",
            max_age=600,
            httponly=True,
            samesite="lax",
        )
        return resp

    @app.get("/auth/github/callback")
    def github_callback(
        request: Request,
        user: CurrentUser,
        code: str = "",
        state: str = "",
        error: str = "",
    ) -> Response:
        nxt = request.cookies.get("gh_oauth_next") or "/projects/new"
        nxt = nxt if nxt.startswith("/") else "/projects/new"
        if error or not code:
            return redirect("/settings?github_error=denied")
        if not state or state != request.cookies.get("gh_oauth_state"):
            return redirect("/settings?github_error=state")
        try:
            token = github_oauth.exchange_code_for_token(code, _github_callback_uri(request))
            login = github_oauth.viewer_login(token)
        except github_oauth.GitHubOAuthError:
            return redirect("/settings?github_error=exchange")
        repo = get_repo(request)
        try:
            repo.save_provider_secret(user.id, github_oauth.PROVIDER, token)
            if login:
                repo.save_provider_secret(user.id, github_oauth.LOGIN_PROVIDER, login)
        except SecretEncryptionUnavailable:
            return redirect("/settings?github_error=encryption")
        resp = redirect(nxt)
        resp.delete_cookie("gh_oauth_state")
        resp.delete_cookie("gh_oauth_next")
        return resp

    @app.post("/auth/github/disconnect")
    def github_disconnect(request: Request, user: CurrentUser) -> Response:
        repo = get_repo(request)
        repo.delete_provider_secret(user.id, github_oauth.PROVIDER)
        repo.delete_provider_secret(user.id, github_oauth.LOGIN_PROVIDER)
        return redirect("/settings")

    @app.get("/api/github/repos")
    def github_repos_api(request: Request, user: CurrentUser) -> JSONResponse:
        token = _github_token(get_repo(request), user)
        if not token:
            return JSONResponse({"connected": False, "repos": []})
        try:
            repos = github_oauth.list_repos(token)
        except github_oauth.GitHubOAuthError as exc:
            return JSONResponse({"connected": True, "error": str(exc), "repos": []})
        return JSONResponse(
            {
                "connected": True,
                "repos": [
                    {
                        "full_name": r.full_name,
                        "private": r.private,
                        "default_branch": r.default_branch,
                    }
                    for r in repos
                ],
            }
        )

    @app.get("/agents", response_class=HTMLResponse)
    def agents_page(request: Request, user: CurrentUser) -> HTMLResponse:
        return render(
            request,
            "agents.html",
            user=user,
            agents=agent_catalog_for_run(_latest_user_run(get_repo(request), user.id)),
            agent_providers=AGENT_PROVIDER_OPTIONS,
            agent_models=AGENT_MODEL_OPTIONS,
            gemini_models=GEMINI_MODEL_OPTIONS,
            codex_models=CODEX_MODEL_OPTIONS,
            reasoning_options=REASONING_OPTIONS,
        )

    @app.get("/artifacts", response_class=HTMLResponse)
    def artifacts_index(request: Request, user: CurrentUser) -> HTMLResponse:
        projects = [
            project
            for project in get_repo(request).list_projects_for_user(user.id)
            if project.visibility != "public_demo"
        ]
        return render(
            request,
            "artifacts_index.html",
            user=user,
            projects=projects_with_display_requests(get_repo(request), user.id, projects),
            public_demos=projects_with_display_requests(
                get_repo(request), user.id, get_repo(request).list_public_demo_projects()
            ),
        )

    @app.get("/system-check", response_class=HTMLResponse)
    def system_check(request: Request, user: CurrentUser) -> HTMLResponse:
        return render(
            request,
            "system_check.html",
            user=user,
            checks=system_checks(
                openai_key_configured=bool(user_openai_key(get_repo(request), user)),
                gemini_key_configured=bool(user_or_platform_gemini_key(get_repo(request), user)),
            ),
        )

    @app.get("/artifacts/{run_id}/by-id/{artifact_id}", response_class=HTMLResponse)
    def artifact_view_by_id(
        request: Request,
        run_id: int,
        artifact_id: str,
        user: CurrentUser,
        raw: bool = False,
    ) -> HTMLResponse:
        run = get_repo(request).get_run_for_user(run_id, user.id)
        if not run:
            raise HTTPException(status_code=404)
        record = get_repo(request).get_artifact_record(run_id, artifact_id)
        if record is None:
            raise HTTPException(status_code=404)
        content = get_repo(request).get_artifact_content(run_id, artifact_id)
        try:
            payload = artifact_payload_for_record(record, content)
        except (OSError, ValueError):
            raise HTTPException(status_code=404) from None
        artifact_title = record.label
        artifact_path = record.relative_path
        if raw and payload["kind"] == "html":
            return HTMLResponse(
                html_report_document(str(payload["content"])),
                headers={
                    "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
                    "X-Content-Type-Options": "nosniff",
                },
            )
        return render(
            request,
            "artifact_view.html",
            user=user,
            run=run,
            artifact_path=artifact_path,
            artifact_title=artifact_title,
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
        repo = get_repo(request)
        work_items = repo.list_work_items(run.id)
        business_artifacts = canonical_artifacts_for_run(
            run_dir,
            repo.list_artifact_records(run.id),
            work_items,
        )[0]
        work_items = repo.list_work_items(run.id)
        detail = task_detail_from_work_items(
            task_id,
            work_items,
            business_artifacts,
            repo.list_activity_events(run.id),
            open_duration_end_at="" if _run_status_running(run.status) else run.updated_at,
        )
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
        repo = get_repo(request)
        artifact_records = repo.list_artifact_records(run.id)
        activity_events = repo.list_activity_events(run.id)
        status_value = run.status
        running = _run_status_running(run.status)
        completed = _run_status_completed(run.status)
        work_items = repo.list_work_items(run.id)
        business_artifacts = canonical_artifacts_for_run(
            Path(run.run_dir), artifact_records, work_items
        )[0]
        overview = delivery_overview_from_work_items(
            run_id=str(run.id),
            work_items=work_items,
            artifacts=business_artifacts,
            status=status_value,
            published_url=run.generated_app_url,
        )
        return JSONResponse(
            {
                "status": status_label(status_value),
                "stage": status_label(overview.stage),
                "active_owner": _active_owner_label(overview),
                "running": running,
                "completed": completed,
                "events": len(activity_events),
            }
        )

    @app.get("/api/runs/{run_id}/logs")
    def run_logs(
        request: Request,
        run_id: int,
        user: CurrentUser,
        task_id: str = "",
    ) -> JSONResponse:
        run = get_repo(request).get_run_for_user(run_id, user.id)
        if not run:
            raise HTTPException(status_code=404)
        if not task_id.strip():
            return JSONResponse({"logs": [], "groups": []})
        repo = get_repo(request)
        activity_events = repo.list_activity_events(
            run.id,
            work_item_id=task_id,
        )
        return JSONResponse(
            {
                "logs": rendered_log_entries_from_db_events(activity_events),
                "groups": activity_groups_from_db_events(activity_events),
            }
        )

    @app.get("/api/runs/{run_id}/trace")
    def run_trace(
        request: Request,
        run_id: int,
        user: CurrentUser,
    ) -> JSONResponse:
        repo = get_repo(request)
        run = repo.get_run_for_user(run_id, user.id)
        if not run:
            raise HTTPException(status_code=404)
        run_events = repo.list_run_events(run.id)
        tool_call_events = repo.list_tool_call_events(run.id)
        model_call_events = repo.list_model_call_events(run.id)
        return JSONResponse(
            {
                "run_events": [event.to_dict() for event in run_events],
                "tool_call_events": [event.to_dict() for event in tool_call_events],
                "model_call_events": [event.to_dict() for event in model_call_events],
                "summary": trace_summary(run_events, tool_call_events, model_call_events),
            }
        )

    @app.post("/api/format-request")
    async def format_request(request: Request, user: CurrentUser) -> JSONResponse:
        form = await request.form()
        text = str(form.get("text", ""))
        api_key = formatter_gemini_key(get_repo(request), user)
        if not api_key.strip():
            return JSONResponse(
                {
                    "ok": False,
                    "source": "gemini",
                    "formatted": text,
                    "message": (
                        "Gemini formatting is not configured yet. Your text was not changed."
                    ),
                }
            )
        try:
            formatted = format_request_text_with_llm(text, api_key=api_key)
        except GeminiFormatterUnavailable:
            return JSONResponse(
                {
                    "ok": False,
                    "source": "gemini",
                    "formatted": text,
                    "message": (
                        "Sorry, Gemini formatting is not reachable right now. "
                        "Your text was not changed."
                    ),
                }
            )
        return JSONResponse(
            {
                "ok": True,
                "source": "gemini",
                "formatted": formatted,
                "message": "Formatted with Gemini.",
            }
        )

    return app


def get_repo(request: Request) -> ConsoleRepository:
    return request.app.state.repo


def _github_token(repo: ConsoleRepository, user: User) -> str:
    """The user's decrypted GitHub OAuth access token, or '' when not connected."""
    cred = repo.get_provider_secret(user.id, github_oauth.PROVIDER)
    if cred is None:
        return ""
    try:
        return decrypt_secret(cred.encrypted_value)
    except Exception:
        return ""


def _github_login(repo: ConsoleRepository, user: User) -> str:
    """The connected GitHub username (for display), or '' when not connected."""
    cred = repo.get_provider_secret(user.id, github_oauth.LOGIN_PROVIDER)
    if cred is None:
        return ""
    try:
        return decrypt_secret(cred.encrypted_value)
    except Exception:
        return ""


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


def set_session_cookie(response: Response, token: str, *, secure: bool = False) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=14 * 24 * 60 * 60,
    )


def _trust_proxy_headers() -> bool:
    return os.getenv("AGENTIC_TRUST_PROXY_HEADERS", "").lower() == "true"


def _cookie_secure(request: Request) -> bool:
    """Mark the session cookie Secure over HTTPS, or when explicitly forced.

    Behind a TLS-terminating proxy the direct scheme is http, so X-Forwarded-Proto
    is honored only when AGENTIC_TRUST_PROXY_HEADERS is set for a trusted proxy.
    """

    if os.getenv("AGENTIC_COOKIE_SECURE", "").lower() == "true":
        return True
    if request.url.scheme == "https":
        return True
    if _trust_proxy_headers():
        return request.headers.get("x-forwarded-proto", "").lower() == "https"
    return False


def _client_ip(request: Request) -> str:
    """Resolve the client IP, honoring X-Forwarded-For only behind a trusted proxy.

    Trusting the header unconditionally would let a client spoof its source and
    evade the login rate limit, so it is gated on AGENTIC_TRUST_PROXY_HEADERS.
    """

    if _trust_proxy_headers():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def get_visible_project_or_404(request: Request, project_id: int, user: User) -> Project:
    project = get_repo(request).get_project_for_user(project_id, user.id)
    if project is None:
        raise HTTPException(status_code=404)
    return project


def _run_showcase_ready(repo: ConsoleRepository, run: Run | None) -> bool:
    if run is None or not _run_status_completed(run.status):
        return False
    artifacts = repo.list_artifact_records(run.id)
    has_release_report = any(
        str(getattr(record, "artifact_type", "")) == "release_report" for record in artifacts
    )
    return bool(run.generated_app_url or has_release_report)


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
        "agents": agent_catalog_for_run(run),
        "overview": None,
        "overview_blockers": [],
        "project_request_html": render_markdown(_project_request_text(project, run)),
        "sprint_board_groups": [],
        "run_running": False,
        "run_completed": False,
        "can_restart": False,
        "can_continue": False,
        "showcase_ready": False,
        "run_timing": {},
    }
    if run:
        run_dir = Path(run.run_dir)
        repo = get_repo(request)
        artifact_records = repo.list_artifact_records(run.id)
        work_items = repo.list_work_items(run.id)
        activity_events = repo.list_activity_events(run.id)
        _sync_canonical_run_completion(repo, project, run)
        business_artifacts = canonical_artifacts_for_run(run_dir, artifact_records, work_items)[0]
        canonical_status = run.status
        run_running = _run_status_running(run.status)
        run_completed = _run_status_completed(run.status)
        open_duration_end_at = "" if run_running else run.updated_at
        board = board_cards_from_work_items(
            work_items,
            business_artifacts,
            activity_events,
            open_duration_end_at=open_duration_end_at,
        )
        overview = delivery_overview_from_work_items(
            run_id=str(run.id),
            work_items=work_items,
            artifacts=business_artifacts,
            status=canonical_status,
            published_url=project.generated_app_url or run.generated_app_url,
        )
        context.update(
            {
                "overview": overview,
                "overview_blockers": user_facing_blockers(overview.blockers),
                "board": board,
                "sprints": board_groups_from_work_items(
                    work_items,
                    business_artifacts,
                    activity_events,
                    open_duration_end_at=open_duration_end_at,
                ),
                "work_plan_groups": work_plan_groups_from_work_items(
                    work_items,
                    business_artifacts,
                    activity_events,
                    open_duration_end_at=open_duration_end_at,
                ),
                "sprint_board_groups": sprint_board_groups_from_work_items(
                    work_items,
                    business_artifacts,
                    activity_events,
                    open_duration_end_at=open_duration_end_at,
                ),
                "business_artifacts": business_artifacts,
                "business_artifact_groups": artifact_owner_groups(business_artifacts),
                "business_artifact_phase_groups": artifact_phase_groups(business_artifacts),
                "task_report_groups": task_report_groups_from_work_items(
                    work_items,
                    business_artifacts,
                    activity_events,
                    open_duration_end_at=open_duration_end_at,
                ),
                "technical_artifacts": [],
                "logs": rendered_log_entries_from_db_events(activity_events),
                "activity_groups": activity_groups_from_db_events(activity_events),
                "run_running": run_running,
                "run_completed": run_completed,
                "can_restart": (
                    project.owner_user_id == user.id
                    and project.visibility != "public_demo"
                    and not run_running
                ),
                "can_continue": (
                    project.owner_user_id == user.id
                    and project.visibility != "public_demo"
                    and not run_running
                    and not _run_delivery_done(run.status)
                ),
                "showcase_ready": _run_showcase_ready(repo, run),
                "run_timing": run_timing_from_work_items(
                    work_items,
                    activity_events,
                    completed=run_completed,
                ),
            }
        )
    return render(request, "project_workspace.html", **context)


def _sync_canonical_run_completion(
    repo: ConsoleRepository,
    project: Project,
    run: Run,
) -> None:
    """Mirror canonical run status from the DB run row to the project row."""

    if run.status and project.status != run.status:
        repo.update_project_status(project.id, run.status)


def _run_status_running(status: str) -> bool:
    return status in {"starting", "running", "initialized"}


def _run_status_completed(status: str) -> bool:
    normalized = status.lower()
    if normalized in {"stopped", "complete", "completed", "failed", "blocked"}:
        return True
    return any(token in normalized for token in ("blocked", "failed", "complete"))


def _run_delivery_done(status: str) -> bool:
    normalized = status.strip().lower()
    return normalized in {"complete", "completed", "done", "delivery_completed"} or (
        "completed" in normalized and "blocked" not in normalized and "failed" not in normalized
    )


def _active_owner_label(overview: Any) -> str:
    active_id = str(getattr(overview, "active_work_item_id", "") or "")
    if not active_id:
        return ""
    for feature in getattr(overview, "features", []) or []:
        if str(getattr(feature, "work_item_id", "") or "") == active_id:
            return str(getattr(feature, "owner", "") or "")
    return ""


def _project_request_text(project: Project, _run: Run | None) -> str:
    return project.request_text.strip()


def projects_with_display_requests(
    repo: ConsoleRepository,
    user_id: int,
    projects: list[Project],
) -> list[SimpleNamespace]:
    rows: list[SimpleNamespace] = []
    for project in projects:
        run = repo.latest_run_for_project(project.id, user_id)
        display_request = _preview_text(_project_request_text(project, run))
        rows.append(SimpleNamespace(**asdict(project), display_request=display_request))
    return rows


def _preview_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        cleaned = cleaned.lstrip("#").strip()
        if cleaned.lower() in {"product request", "summary", "requirements", "notes"}:
            continue
        if cleaned.startswith("- "):
            cleaned = cleaned[2:].strip()
        lines.append(cleaned)
    return " ".join(lines).strip()


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
    form_values: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    values = form_values or new_project_form_values()
    repo = get_repo(request)
    gh_token = _github_token(repo, user)
    gh_repos: list[github_oauth.Repo] = []
    if gh_token:
        try:
            gh_repos = github_oauth.list_repos(gh_token)
        except github_oauth.GitHubOAuthError:
            gh_repos = []
    return render(
        request,
        "new_project.html",
        user=user,
        formatted="",
        error=error,
        form_values=values,
        github_configured=github_oauth.is_configured(),
        github_connected=bool(gh_token),
        github_login=_github_login(repo, user),
        github_repos=gh_repos,
        credential=repo.get_provider_secret(user.id, "openai"),
        gemini_credential=repo.get_provider_secret(user.id, "google_gemini"),
        platform_gemini_configured=bool(platform_agent_gemini_key()),
        formatter_gemini_configured=bool(platform_formatter_gemini_key()),
        agent_providers=AGENT_PROVIDER_OPTIONS,
        agent_models=AGENT_MODEL_OPTIONS,
        gemini_models=GEMINI_MODEL_OPTIONS,
        codex_models=CODEX_MODEL_OPTIONS,
        reasoning_options=REASONING_OPTIONS,
        service_tiers=CODEX_SERVICE_TIERS,
        status_code=status_code,
    )


def new_project_form_values(
    *,
    name: str = "",
    request_text: str = "",
    mode: str = "simple_prototype",
    complexity: str = "simple",
    agent_provider: str = "google_gemini",
    agent_model: str = "gemini-3.1-flash-lite",
    codex_model: str = DEFAULT_CODEX_MODEL,
    codex_reasoning: str = "medium",
    service_tier: str = "standard",
    board_adapter: str = "internal",
    repository: str = "",
    project_owner: str = "",
) -> dict[str, str]:
    agent_provider = normalize_agent_provider(agent_provider)
    return {
        "name": name,
        "request_text": request_text,
        "mode": mode,
        "complexity": complexity,
        "agent_provider": agent_provider,
        "agent_model": normalize_agent_model(agent_provider, agent_model),
        "codex_model": normalize_codex_model(codex_model),
        "codex_reasoning": codex_reasoning,
        "service_tier": service_tier,
        "board_adapter": normalize_board_adapter(board_adapter),
        "repository": repository.strip(),
        "project_owner": project_owner.strip(),
    }


def normalize_board_adapter(value: str) -> str:
    return value if value in {"internal", "github"} else "internal"


def _maybe_create_board_connection(
    repo: ConsoleRepository,
    *,
    project_id: int,
    board_adapter: str,
    repository: str,
    project_owner: str,
    token_ref: str = "",
) -> None:
    """Record a project-scoped GitHub work-system connection (best-effort).

    Stores the repository and board owner; the run mirror provisions a fresh
    Project board on first use and caches its ids back onto the connection. A
    failure here must not block project creation — the run still delivers on
    ADL's internal board.
    """
    if board_adapter != "github" or not repository.strip():
        return
    owner = project_owner.strip() or repository.strip().split("/", 1)[0]
    metadata: dict[str, Any] = {"owner": owner}
    try:
        repo.create_work_system_connection(
            project_id=project_id,
            system="github",
            name="GitHub",
            repository=repository.strip(),
            token_ref=token_ref,
            metadata=metadata,
        )
    except Exception as exc:  # best-effort: never block project creation
        LOGGER.warning("Could not create GitHub board connection: %s", exc)


def normalize_agent_provider(provider: str) -> str:
    return provider if provider in {"openai", "google_gemini"} else "google_gemini"


def normalize_agent_model(provider: str, model: str) -> str:
    if provider == "google_gemini":
        return model if model in GEMINI_MODEL_OPTIONS else "gemini-3.1-flash-lite"
    return model if model in AGENT_MODEL_OPTIONS else "gpt-4.1"


def normalize_codex_model(model: str) -> str:
    normalized = str(model or "").strip()
    if normalized in CODEX_MODEL_OPTIONS:
        return normalized
    allowed = ", ".join(CODEX_MODEL_OPTIONS)
    raise ValueError(
        f"Unsupported Codex model '{normalized or '<empty>'}'. Choose one of: {allowed}."
    )


def agent_catalog_for_run(run: Run | None) -> list[dict[str, str]]:
    env = _run_env(run)
    agent_provider = normalize_agent_provider(env.get("AGENT_LLM_PROVIDER", "google_gemini"))
    agent_model = normalize_agent_model(
        agent_provider,
        env.get("AGENT_LLM_MODEL", "gemini-3.1-flash-lite"),
    )
    env_codex_model = (
        env.get("AGENT_CODEX_MODEL")
        or env.get("FULLSTACK_CODEX_MODEL")
        or env.get("BUSINESS_ANALYST_CODEX_MODEL")
        or DEFAULT_CODEX_MODEL
    )
    try:
        codex_model = normalize_codex_model(env_codex_model)
    except ValueError:
        codex_model = f"unsupported: {env_codex_model}"
    return agent_catalog(
        agent_provider=agent_provider,
        agent_model=agent_model,
        codex_model=codex_model,
        codex_reasoning=env.get("AGENTIC_CODEX_REASONING_EFFORT", "medium"),
    )


def _latest_user_run(repo: ConsoleRepository, user_id: int) -> Run | None:
    latest: Run | None = None
    for project in repo.list_projects_for_user(user_id):
        run = repo.latest_run_for_project(project.id, user_id)
        if run is None:
            continue
        if latest is None or run.created_at > latest.created_at:
            latest = run
    return latest


def user_openai_key(repo: ConsoleRepository, user: User) -> str:
    credential = repo.get_provider_secret(user.id, "openai")
    if credential is None:
        return ""
    if credential.storage_mode == "encrypted":
        return decrypt_secret(credential.encrypted_value)
    if credential.storage_mode == "local_demo":
        return credential.encrypted_value
    return ""


def user_gemini_key(repo: ConsoleRepository, user: User) -> str:
    credential = repo.get_provider_secret(user.id, "google_gemini")
    if credential is None:
        return ""
    if credential.storage_mode == "encrypted":
        return decrypt_secret(credential.encrypted_value)
    if credential.storage_mode == "local_demo":
        return credential.encrypted_value
    return ""


def platform_gemini_key() -> str:
    return os.getenv("GOOGLE_API_KEY", "").strip() or os.getenv("GEMINI_API_KEY", "").strip()


def platform_agent_gemini_key() -> str:
    return os.getenv("AGENT_GEMINI_API_KEY", "").strip() or platform_gemini_key()


def platform_formatter_gemini_key() -> str:
    return os.getenv("GEMINI_FORMATTER_API_KEY", "").strip() or platform_gemini_key()


def user_or_platform_gemini_key(repo: ConsoleRepository, user: User) -> str:
    return user_gemini_key(repo, user) or platform_agent_gemini_key()


def formatter_gemini_key(repo: ConsoleRepository, user: User) -> str:
    return user_gemini_key(repo, user) or platform_formatter_gemini_key()


def prepare_run_environment(
    run_dir: Path,
    *,
    agent_model: str,
    agent_reasoning: str,
    codex_model: str,
    codex_reasoning: str,
    service_tier: str,
    gemini_api_key: str = "",
    agent_provider: str = "google_gemini",
) -> Path:
    agent_provider = normalize_agent_provider(agent_provider)
    agent_model = normalize_agent_model(agent_provider, agent_model)
    codex_model = normalize_codex_model(codex_model)
    values = {
        CODEX_AUTH_MODE_ENV: codex_auth_mode_from_env(),
        "AGENT_LLM_PROVIDER": agent_provider,
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


def _codex_start_preflight(
    repo: ConsoleRepository,
    user: User,
    *,
    ignore_project_id: int | None = None,
) -> str:
    if codex_auth_mode_from_env() != CODEX_AUTH_MODE_USER_CHATGPT:
        return ""
    connection = repo.get_codex_auth_connection(user.id)
    if connection is None or connection.status != "connected":
        return "Connect your Codex account in Settings before starting a run."
    active_statuses = {
        "starting",
        "running",
        "initialized",
        "head",
        "sprint_running",
        "deployment_running",
    }
    for project in repo.list_projects_for_user(user.id):
        if ignore_project_id is not None and project.id == ignore_project_id:
            continue
        run = repo.latest_run_for_project(project.id, user.id)
        if run and run.status.strip().lower() in active_statuses:
            return "Your Codex account already has a run in progress. Stop or finish it first."
    return ""


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
        or DEFAULT_CODEX_MODEL
    )
    saved_agent_model = latest_env.get("AGENT_LLM_MODEL", "")
    saved_agent_provider = latest_env.get("AGENT_LLM_PROVIDER", "")
    if not saved_agent_provider and saved_agent_model:
        saved_agent_provider = (
            "google_gemini" if saved_agent_model.startswith("gemini-") else "openai"
        )
    return {
        "agent_provider": saved_agent_provider or "google_gemini",
        "agent_model": saved_agent_model or "gemini-3.1-flash-lite",
        "agent_reasoning": agent_reasoning,
        "codex_model": normalize_codex_model(codex_model),
        "codex_reasoning": latest_env.get("AGENTIC_CODEX_REASONING_EFFORT", "medium"),
        "service_tier": latest_env.get("AGENTIC_CODEX_SERVICE_TIER", "standard"),
    }


def _run_env(run: Run | None) -> dict[str, str]:
    if run is None:
        return {}
    return read_env_keys(agent_runtime_env_path(Path(run.run_dir)))


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
    from agentic_company.integrations.codex.runner import codex_runtime_config_summary

    logging.getLogger("agentic_company.console").info(
        "Codex worker defaults: %s", codex_runtime_config_summary()
    )
    host = os.getenv("AGENTIC_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("AGENTIC_WEB_PORT", "8503"))
    uvicorn.run(
        "agentic_company.console.web.app:create_app",
        host=host,
        port=port,
        reload=False,
        factory=True,
        access_log=False,
    )


if __name__ == "__main__":
    main()
