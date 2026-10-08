from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import typer

from b2t import __version__
from b2t.bootstrap import ensure_bootstrap, run_bootstrap
from b2t.cli_progress import TqdmTaskRenderer
from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.downloads import DownloadBatchService
from b2t.factory import build_pipeline
from b2t.i18n import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, dependency_sync_guidance, resolve_language, tr
from b2t.inputs import parse_source_list
from b2t.library import WorkspaceLibrary
from b2t.tasks import TaskService
from b2t.user_config import AppConfig


def create_app(language: str = DEFAULT_LANGUAGE) -> typer.Typer:
    app = typer.Typer(
        add_completion=False,
        no_args_is_help=True,
        help=tr(language, "app_help"),
    )

    @app.callback(invoke_without_command=True)
    def version_callback(
        version: bool = typer.Option(
            False,
            "--version",
            help=tr(language, "show_version"),
            is_eager=True,
        ),
    ) -> None:
        if version:
            typer.echo(__version__)
            raise typer.Exit()

    @app.command("login", help=tr(language, "cmd_login_help"))
    def bilibili_login(
        status: bool = typer.Option(False, "--status", help=tr(language, "opt_login_status_help")),
        timeout: int = typer.Option(180, "--timeout", min=15, max=180, help=tr(language, "opt_login_timeout_help")),
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Scan with the Bilibili app and save login cookies for this workspace."""
        from rich.console import Console
        from b2t.login import LoginError, check_login, login_with_qr

        selected_language = _detect_preferred_language(workspace)
        target = Settings.from_workspace(workspace).cookie_path
        try:
            if status:
                authenticated = check_login(target)
                typer.echo(tr(selected_language, "login_valid" if authenticated else "login_not_authenticated"))
                if not authenticated:
                    raise typer.Exit(code=1)
            else:
                login_with_qr(target=target, console=Console(), language=selected_language, timeout=timeout)
        except typer.Exit:
            raise
        except KeyboardInterrupt:
            typer.echo(tr(selected_language, "login_cancelled"), err=True)
            raise typer.Exit(code=130) from None
        except LoginError as error:
            typer.secho(tr(selected_language, error.key, **error.details), err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from None
        except Exception as error:
            typer.secho(tr(selected_language, "login_unexpected_error", kind=type(error).__name__), err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from None

    @app.command("transcribe", help=tr(language, "cmd_transcribe_help"))
    @app.command("tx", hidden=True)
    def transcribe(
        source: str = typer.Argument(..., help=tr(language, "arg_source_help")),
        provider: str | None = typer.Option(None, "--provider", help=tr(language, "opt_provider_help")),
        model: str | None = typer.Option(None, "--model", help=tr(language, "opt_model_help")),
        audio_language: str | None = typer.Option(None, "--audio-language", help=tr(language, "opt_audio_language_help")),
        device: str | None = typer.Option(None, "--device", help=tr(language, "opt_device_help")),
        prompt: str = typer.Option("", "--prompt", help=tr(language, "opt_prompt_help")),
        output: Path | None = typer.Option(None, "--output", help=tr(language, "opt_output_help")),
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Download or open media, then transcribe it with the selected provider."""
        try:
            settings, config = _load_runtime(workspace=workspace, provider=provider, model=model)
            renderer = TqdmTaskRenderer(config.language)
            service = _build_task_service(
                settings=settings,
                config=config,
                provider=provider,
                model=model,
                audio_language=audio_language,
                device=device,
            )
            task = service.submit_transcription(
                source=source,
                provider=provider or config.default_provider,
                model=model or config.default_model,
                prompt=prompt,
                listener=renderer,
            )
            typer.echo(tr(config.language, "task_submitted", task_id=task.id))
            task = service.wait_for_task(task.id)
            if task.video_id is None:
                raise RuntimeError("transcription completed but no video record was created")
            video = service.database.get_video(task.video_id)
            if video is None:
                raise RuntimeError(f"video record not found: {task.video_id}")
            transcript = service.library.load_active_transcript(task.video_id)
        except Exception as exc:
            message = tr(_detect_preferred_language(workspace), "error_prefix", message=exc)
            typer.secho(message, err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc

        typer.echo(tr(config.language, "transcript_saved", path=transcript["file_path"]))
        typer.echo(tr(config.language, "metadata_saved", path=video["metadata_path"]))

    @app.command("batch", help=tr(language, "cmd_batch_help"))
    def batch_transcribe(
        sources: list[str] | None = typer.Argument(None, help=tr(language, "arg_sources_help")),
        source_file: Path | None = typer.Option(None, "--file", "-f", help=tr(language, "opt_source_file_help")),
        provider: str | None = typer.Option(None, "--provider", help=tr(language, "opt_provider_help")),
        model: str | None = typer.Option(None, "--model", help=tr(language, "opt_model_help")),
        audio_language: str | None = typer.Option(None, "--audio-language", help=tr(language, "opt_audio_language_help")),
        device: str | None = typer.Option(None, "--device", help=tr(language, "opt_device_help")),
        prompt: str = typer.Option("", "--prompt", help=tr(language, "opt_prompt_help")),
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Submit multiple transcription tasks from arguments or a newline-separated file."""
        selected_language = _detect_preferred_language(workspace)
        try:
            settings, config = _load_runtime(workspace=workspace, provider=provider, model=model)
            service = _build_task_service(
                settings=settings,
                config=config,
                provider=provider,
                model=model,
                audio_language=audio_language,
                device=device,
            )
            source_values = _collect_batch_sources(sources or [], source_file)
            tasks = [
                service.submit_transcription(
                    source=source,
                    provider=provider or config.default_provider,
                    model=model or config.default_model,
                    prompt=prompt,
                )
                for source in source_values
            ]
            typer.echo(tr(config.language, "batch_submitted", count=len(tasks)))
            for task in tasks:
                typer.echo(f"{task.id}\t{task.source_input}")

            failed = 0
            for task in tasks:
                try:
                    completed = service.wait_for_task(task.id)
                except Exception as exc:
                    failed += 1
                    typer.secho(
                        tr(config.language, "batch_task_failed", task_id=task.id, message=exc),
                        err=True,
                        fg=typer.colors.RED,
                    )
                    continue
                if completed.status == "completed":
                    typer.echo(tr(config.language, "batch_task_completed", task_id=completed.id))
                else:
                    failed += 1
                    typer.secho(
                        tr(config.language, "batch_task_failed", task_id=completed.id, message=completed.error_message),
                        err=True,
                        fg=typer.colors.RED,
                    )
        except Exception as exc:
            message = tr(selected_language, "error_prefix", message=exc)
            typer.secho(message, err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from exc

        if failed:
            raise typer.Exit(code=1)

    @app.command("download", help=tr(language, "cmd_download_help"))
    @app.command("dl", hidden=True)
    def download_videos(
        sources: list[str] | None = typer.Argument(None, help=tr(language, "arg_sources_help")),
        source_file: Path | None = typer.Option(None, "--file", "-f", help=tr(language, "opt_source_file_help")),
        from_tasks: bool = typer.Option(False, "--from-tasks", help=tr(language, "opt_from_tasks_help")),
        dry_run: bool = typer.Option(False, "--dry-run", help=tr(language, "opt_download_dry_run_help")),
        transcribe_after: bool = typer.Option(False, "--transcribe", help=tr(language, "opt_download_transcribe_help")),
        provider: str | None = typer.Option(None, "--provider", help=tr(language, "opt_provider_help")),
        model: str | None = typer.Option(None, "--model", help=tr(language, "opt_model_help")),
        audio_language: str | None = typer.Option(None, "--audio-language", help=tr(language, "opt_audio_language_help")),
        device: str | None = typer.Option(None, "--device", help=tr(language, "opt_device_help")),
        prompt: str = typer.Option("", "--prompt", help=tr(language, "opt_prompt_help")),
        attempts: int = typer.Option(3, "--attempts", min=1, max=10, help=tr(language, "opt_download_attempts_help")),
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Resume video downloads, deduplicating inputs and skipping complete files."""
        selected_language = _detect_preferred_language(workspace)
        try:
            settings = Settings.from_workspace(workspace)
            database = AppDatabase(settings)
            service = DownloadBatchService(settings, database, attempts=attempts)
            config = AppConfig.load(settings)
            selected_provider = (provider or config.default_provider).strip().lower()
            selected_model = (model or config.default_model).strip()
            if not transcribe_after and (provider is not None or model is not None or audio_language is not None or device is not None or prompt):
                raise ValueError("--provider, --model, --audio-language, --device and --prompt require --transcribe")
            if transcribe_after and audio_language is None and selected_provider == "whisper":
                audio_language = config.whisper.audio_language
            if device is not None:
                device = device.strip().lower()
                if selected_provider != "whisper":
                    raise ValueError("--device is currently supported by Whisper only")
                if device not in {"auto", "cpu", "cuda", "mps"}:
                    raise ValueError("Whisper device must be auto, cpu, cuda or mps")
            if audio_language:
                audio_language = audio_language.strip().lower()
            if audio_language and audio_language != "auto" and selected_provider != "whisper":
                raise ValueError("--audio-language is currently supported by Whisper only")
            inputs = _collect_batch_sources(sources or [], source_file) if sources or source_file else []
            plan = service.plan(inputs, from_tasks=from_tasks)
            skipped = sum(item.skip for item in plan)
            typer.echo(tr(selected_language, "download_plan", total=len(plan), skipped=skipped, pending=len(plan) - skipped))
            completed, failed = 0, 0
            transcribed, transcript_skipped, subtitled = 0, 0, 0
            transcription_service = None
            for index, item in enumerate(plan, 1):
                label = tr(selected_language, "download_skip" if item.skip else "download_pending")
                typer.echo(f"[{index}/{len(plan)}] {label}: {item.source.raw_input} → {item.path}")
                transcript_exists = transcribe_after and service.has_transcript(item, provider=selected_provider, model=selected_model, audio_language=audio_language)
                if transcript_exists:
                    transcript_skipped += 1
                    typer.echo(tr(selected_language, "download_transcript_skip", source=item.source.raw_input))
                    continue
                if item.skip and not transcribe_after:
                    continue
                if dry_run:
                    if transcribe_after and not transcript_exists:
                        typer.echo(tr(selected_language, "download_transcript_pending", source=item.source.raw_input))
                    continue
                try:
                    if transcribe_after:
                        if transcription_service is None:
                            pipeline = build_pipeline(settings=settings, config=config, provider=selected_provider, model=selected_model, audio_language=audio_language, device=device)
                            pipeline.downloader = service
                            transcription_service = TaskService(
                                database=database, library=WorkspaceLibrary(settings, database),
                                pipeline_factory=lambda selected_provider, selected_model: pipeline,
                            )
                            transcription_service.ensure_indexed()
                        renderer = TqdmTaskRenderer(selected_language) if sys.stderr.isatty() else None
                        typer.echo(tr(selected_language, "download_transcript_pending", source=item.source.raw_input))
                        task = transcription_service.run_transcription(
                            source=item.source.raw_input, provider=selected_provider, model=selected_model,
                            prompt=prompt, listener=renderer,
                        )
                        if task.video_id is None:
                            raise RuntimeError("transcription completed but no video record was created")
                        video = database.get_video(task.video_id)
                        if video and video["engine"] == "bilibili-subtitles":
                            subtitled += 1
                            typer.echo(tr(selected_language, "subtitle_used", source=item.source.raw_input))
                        elif not item.skip:
                            completed += 1
                        transcript = transcription_service.library.load_active_transcript(task.video_id)
                        transcribed += 1
                        typer.echo(tr(selected_language, "transcript_saved", path=transcript["file_path"]))
                    else:
                        renderer = TqdmTaskRenderer(selected_language) if sys.stderr.isatty() else None
                        result = service.run_one(item, progress=renderer)
                        completed += 1
                        typer.echo(tr(selected_language, "download_saved", path=result.video_path))
                except Exception as error:
                    failed += 1
                    typer.secho(tr(selected_language, "error_prefix", message=error), err=True, fg=typer.colors.RED)
            if dry_run:
                typer.echo(tr(selected_language, "download_dry_run"))
            else:
                typer.echo(tr(selected_language, "download_summary", completed=completed, skipped=skipped, failed=failed))
                if transcribe_after:
                    typer.echo(tr(selected_language, "download_transcript_summary", completed=transcribed, skipped=transcript_skipped))
                    typer.echo(tr(selected_language, "subtitle_summary", count=subtitled))
        except KeyboardInterrupt:
            typer.echo(tr(selected_language, "download_interrupted"), err=True)
            raise typer.Exit(code=130)
        except Exception as error:
            typer.secho(tr(selected_language, "error_prefix", message=error), err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1) from error
        if failed:
            raise typer.Exit(code=1)

    @app.command("doctor", help=tr(language, "cmd_doctor_help"))
    @app.command("diag", hidden=True)
    def doctor(
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Print the current runtime requirements and what is missing."""
        selected_language = _detect_preferred_language(workspace)
        ffmpeg = shutil.which("ffmpeg")
        rows: list[tuple[str, str]] = [(tr(selected_language, "doctor_ffmpeg"), ffmpeg or tr(selected_language, "status_missing"))]

        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            rows.insert(0, (tr(selected_language, "doctor_yt_dlp"), tr(selected_language, "status_missing")))
        else:
            rows.insert(0, (tr(selected_language, "doctor_yt_dlp"), tr(selected_language, "status_ok")))

        try:
            import whisper  # noqa: F401
        except ImportError:
            rows.append((tr(selected_language, "doctor_whisper"), tr(selected_language, "status_missing")))
        else:
            rows.append((tr(selected_language, "doctor_whisper"), tr(selected_language, "status_ok")))

        try:
            import funasr_onnx  # noqa: F401
        except ImportError:
            rows.append((tr(selected_language, "doctor_sensevoice"), tr(selected_language, "status_missing")))
        else:
            rows.append((tr(selected_language, "doctor_sensevoice"), tr(selected_language, "status_ok")))

        try:
            import requests  # noqa: F401
        except ImportError:
            rows.append((tr(selected_language, "doctor_requests"), tr(selected_language, "status_missing")))
        else:
            rows.append((tr(selected_language, "doctor_requests"), tr(selected_language, "status_ok")))

        for label, status in rows:
            typer.echo(f"{label}: {status}")

    @app.command("bootstrap", help=tr(language, "cmd_bootstrap_help"))
    @app.command("init", hidden=True)
    def bootstrap(
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
        sync_only: bool = typer.Option(False, "--sync-only", help=tr(language, "bootstrap_sync_only")),
    ) -> None:
        """Create or update the local bili2text config."""
        settings = Settings.from_workspace(workspace)
        if sync_only and not settings.config_path.exists():
            typer.secho(tr(_detect_preferred_language(workspace), "bootstrap_sync_only_missing_config"), err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)
        run_bootstrap(settings=settings, interactive=not sync_only)

    @app.command("web", help=tr(language, "cmd_web_help"))
    @app.command("ui", hidden=True)
    def web_ui(
        host: str = typer.Option("127.0.0.1", "--host", help=tr(language, "opt_host_help")),
        port: int = typer.Option(8000, "--port", help=tr(language, "opt_port_help")),
        provider: str | None = typer.Option(None, "--provider", help=tr(language, "opt_provider_help")),
        model: str | None = typer.Option(None, "--model", help=tr(language, "opt_model_help")),
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Launch the plain HTML web interface."""
        _run_server(host=host, port=port, provider=provider, model=model, workspace=workspace)

    @app.command("server", help=tr(language, "cmd_server_help"))
    @app.command("srv", hidden=True)
    def server_mode(
        host: str = typer.Option("0.0.0.0", "--host", help=tr(language, "opt_host_help")),
        port: int = typer.Option(8000, "--port", help=tr(language, "opt_port_help")),
        provider: str | None = typer.Option(None, "--provider", help=tr(language, "opt_provider_help")),
        model: str | None = typer.Option(None, "--model", help=tr(language, "opt_model_help")),
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Launch the server feature for Docker or LAN deployment."""
        _run_server(host=host, port=port, provider=provider, model=model, workspace=workspace)

    @app.command("window", help=tr(language, "cmd_window_help"))
    @app.command("win", hidden=True)
    def window_mode(
        provider: str | None = typer.Option(None, "--provider", help=tr(language, "opt_provider_help")),
        model: str | None = typer.Option(None, "--model", help=tr(language, "opt_model_help")),
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Launch the Tk window feature."""
        from b2t.window_app import run_window

        settings, config = _load_runtime(workspace=workspace, provider=provider, model=model)

        run_window(
            pipeline_factory=lambda selected_provider, selected_model, selected_workspace: build_pipeline(
                settings=Settings.from_workspace(selected_workspace or settings.workspace_root),
                config=config,
                provider=selected_provider or provider or config.default_provider,
                model=selected_model or model or config.default_model,
            ),
            default_provider=provider or config.default_provider,
            default_model=model or config.default_model,
            default_workspace=settings.workspace_root,
            default_prompt=config.whisper.initial_prompt,
            language=config.language,
        )

    @app.command("language", help=tr(language, "cmd_language_help"))
    @app.command("lang", hidden=True)
    def language_command(
        value: str = typer.Argument(..., help=tr(language, "opt_language_help")),
        workspace: Path | None = typer.Option(None, "--workspace", help=tr(language, "opt_workspace_help")),
    ) -> None:
        """Switch the preferred interface language."""
        resolved = resolve_language(value)
        if not resolved:
            typer.secho(tr(language, "unsupported_language", language=value), err=True, fg=typer.colors.RED)
            raise typer.Exit(code=1)

        settings = Settings.from_workspace(workspace)
        config = AppConfig.load(settings)
        config.language = resolved
        config.save(settings)
        typer.echo(tr(resolved, "language_updated", language=SUPPORTED_LANGUAGES[resolved]))

    return app


def main() -> None:
    create_app(_detect_preferred_language())(prog_name="bili2text")


def _load_runtime(
    *,
    workspace: Path | None,
    provider: str | None = None,
    model: str | None = None,
    allow_bootstrap: bool = True,
) -> tuple[Settings, AppConfig]:
    settings = Settings.from_workspace(workspace)
    config = ensure_bootstrap(
        settings=settings,
        allow_prompt=allow_bootstrap and sys.stdin.isatty(),
    )
    if provider:
        config.default_provider = provider
    if model:
        config.default_model = model
    return settings, config


def _run_server(*, host: str, port: int, provider: str | None, model: str | None, workspace: Path | None) -> None:
    selected_language = _detect_preferred_language(workspace)
    try:
        import uvicorn
    except ImportError as exc:
        typer.secho(
            tr(
                selected_language,
                "missing_dependency",
                name="web/server",
                guidance=dependency_sync_guidance(selected_language),
            ),
            err=True,
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1) from exc

    from b2t.web import create_app

    settings, config = _load_runtime(workspace=workspace, provider=provider, model=model)
    service = _build_task_service(settings=settings, config=config, provider=provider, model=model)
    app_instance = create_app(
        task_service=service,
        library=service.library,
        database=service.database,
        default_provider=provider or config.default_provider,
        default_model=model or config.default_model,
        language=config.language,
    )
    uvicorn.run(app_instance, host=host, port=port)


def _detect_preferred_language(workspace: Path | None = None) -> str:
    env_language = resolve_language(os.getenv("B2T_LANG"))
    if env_language:
        return env_language

    settings = Settings.from_workspace(workspace)
    if settings.config_path.exists():
        return AppConfig.load(settings).language
    return DEFAULT_LANGUAGE


def _build_task_service(
    *,
    settings: Settings,
    config: AppConfig,
    provider: str | None = None,
    model: str | None = None,
    audio_language: str | None = None,
    device: str | None = None,
) -> TaskService:
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    service = TaskService(
        database=database,
        library=library,
        pipeline_factory=lambda selected_provider, selected_model: build_pipeline(
            settings=settings,
            config=config,
            provider=selected_provider or provider or config.default_provider,
            model=selected_model or model or config.default_model,
            audio_language=audio_language,
            device=device,
        ),
    )
    service.ensure_indexed()
    return service


def _collect_batch_sources(sources: list[str], source_file: Path | None) -> list[str]:
    chunks: list[str] = []
    if source_file is not None:
        chunks.append(source_file.expanduser().read_text(encoding="utf-8"))
    chunks.extend(sources)
    return parse_source_list("\n".join(chunks))


app = create_app(DEFAULT_LANGUAGE)
