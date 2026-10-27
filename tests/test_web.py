import hashlib
from pathlib import Path
import re

from fastapi.testclient import TestClient

from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.library import WorkspaceLibrary
from b2t.models import SourceRef, TranscriptResult
from b2t.tasks import TaskService
from b2t.web import create_app


class FakePipeline:
    def __init__(self, settings: Settings, provider: str, model: str) -> None:
        self.settings = settings
        self.provider = provider
        self.model = model

    def transcribe(self, source: str, *, prompt: str | None = None, output: Path | None = None, progress=None) -> TranscriptResult:
        source_id = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
        transcript_path = self.settings.transcripts_original_dir / f"demo-{source_id}.txt"
        metadata_path = self.settings.metadata_dir / f"demo-{source_id}.json"
        transcript_path.write_text("demo text\n", encoding="utf-8")
        metadata_path.write_text("{}", encoding="utf-8")
        if progress is not None:
            progress.running("transcribing", message="transcribing", stage_progress=1.0)
        return TranscriptResult(
            source=SourceRef(raw_input=source, kind="bilibili", display_name="demo", url=source, bv="BV1xx411c7XD"),
            engine=self.provider,
            model=self.model,
            text="demo text",
            audio_path=self.settings.audio_dir / "demo.wav",
            transcript_path=transcript_path,
            metadata_path=metadata_path,
            video_path=self.settings.downloads_dir / "demo.mp4",
            metadata={"language": "zh", "download": {"title": "Demo Title"}},
        )


def build_test_app(tmp_path: Path, **options):
    settings = Settings.from_workspace(tmp_path / ".b2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    service = TaskService(
        database=database,
        library=library,
        pipeline_factory=lambda provider, model: FakePipeline(settings, provider, model),
    )
    app = create_app(
        task_service=service,
        library=library,
        database=database,
        default_provider="sensevoice",
        default_model="base",
        language="zh-CN",
        **options,
    )
    return app, service, database, library


def test_index_page_renders_form_and_video_list(tmp_path: Path) -> None:
    app, _, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200
    assert "Bilibili 视频转文字" in response.text
    assert 'value="sensevoice"' in response.text
    assert "结果库" in response.text


def test_shared_ui_has_saved_prompt_and_local_assets(tmp_path: Path) -> None:
    app, _, _, _ = build_test_app(tmp_path, default_prompt="中文 & <saved prompt>")
    client = TestClient(app)
    response = client.get("/")
    assert "中文 &amp; &lt;saved prompt&gt;" in response.text
    assert 'type="file"' in response.text
    assert str((tmp_path / ".b2t").resolve()) in response.text
    assert "cdn.tailwindcss.com" not in response.text
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/app.js").status_code == 200


def test_local_files_and_links_share_one_persistent_batch(tmp_path: Path) -> None:
    app, service, database, _ = build_test_app(tmp_path)
    client = TestClient(app)
    response = client.post(
        "/transcribe",
        data={"source": "BV1xx411c7XD", "provider": "sensevoice", "model": "tiny", "prompt": "saved"},
        files=[("media", ("../../语音.wav", b"audio", "audio/wav"))],
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/tasks/batch?ids=")
    tasks = service.list_tasks()
    assert len(tasks) == 2
    uploaded = next(Path(task.source_input) for task in tasks if task.source_input != "BV1xx411c7XD")
    assert uploaded.is_relative_to(tmp_path / ".b2t" / "downloads" / "imports")
    assert uploaded.read_bytes() == b"audio"
    for task in tasks:
        assert service.wait_for_task(task.id).status == "completed"
    assert "已提交 2 个任务" in client.get(response.headers["location"]).text
    assert len(database.list_tasks()) == 2
    assert "BV1xx411c7XD" in client.get("/").text


def test_bad_upload_preserves_form_and_does_not_submit_tasks(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)
    for name, content in [("bad.exe", b"data"), ("empty.wav", b"")]:
        response = client.post(
            "/transcribe", data={"source": "BV1xx411c7XD", "prompt": "keep this"},
            files={"media": (name, content)},
        )
        assert response.status_code == 400
        assert "keep this" in response.text
        assert "BV1xx411c7XD" in response.text
        assert service.list_tasks() == []
    assert list((tmp_path / ".b2t" / "downloads" / "imports").iterdir()) == []


def test_workspace_open_requires_local_mode_and_session_token(tmp_path: Path, monkeypatch) -> None:
    opened = []
    monkeypatch.setattr("b2t.window_app.open_path", opened.append)
    app, _, _, _ = build_test_app(tmp_path, local_actions=True)
    client = TestClient(app)
    token = re.search(r'data-action-token="([^"]+)"', client.get("/").text).group(1)
    assert client.post("/api/workspace/open").status_code == 403
    assert client.post("/api/workspace/open", headers={"X-B2T-Token": "wrong"}).status_code == 403
    assert opened == []
    assert client.post("/api/workspace/open", headers={"X-B2T-Token": token}).status_code == 200
    assert opened == [tmp_path / ".b2t"]
    remote, _, _, _ = build_test_app(tmp_path / "remote")
    assert TestClient(remote).post("/api/workspace/open", headers={"X-B2T-Token": token}).status_code == 403


def test_result_can_be_edited_restored_and_exported(tmp_path: Path) -> None:
    app, service, database, _ = build_test_app(tmp_path)
    client = TestClient(app)
    task = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="small")
    video_id = service.wait_for_task(task.id).video_id
    original = database.list_transcript_versions(video_id)[0]
    for text in ["first edit", "second edit"]:
        assert client.post(f"/videos/{video_id}/edit", data={"text": text}).status_code == 200
    versions = database.list_transcript_versions(video_id)
    assert len({version.file_path for version in versions}) == 3
    assert client.get(f"/videos/{video_id}/export").text == "second edit\n"
    for version in versions:
        assert "text" in client.get(f"/api/videos/{video_id}/versions/{version.id}").json()
    restored = client.post(f"/videos/{video_id}/versions/{original.id}/activate")
    assert restored.status_code == 200
    exported = client.get(f"/videos/{video_id}/export")
    assert exported.text == "demo text\n"
    assert "attachment" in exported.headers["content-disposition"]
    assert client.get("/videos/999/export").status_code == 404
    assert client.post(f"/videos/{video_id}/versions/999/activate").status_code == 404


def test_api_transcribe_returns_task_and_video_can_be_edited(tmp_path: Path) -> None:
    app, service, database, library = build_test_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "sensevoice",
            "model": "tiny",
            "prompt": "",
        },
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    video_response = client.get("/api/videos")
    assert video_response.status_code == 200
    assert len(video_response.json()["items"]) == 1

    transcript_response = client.get(f"/api/videos/{task.video_id}/transcript")
    assert transcript_response.status_code == 200
    assert transcript_response.json()["text"] == "demo text\n"

    update_response = client.put(
        f"/api/videos/{task.video_id}/transcript",
        json={"text": "edited text"},
    )
    assert update_response.status_code == 200

    transcript_after = client.get(f"/api/videos/{task.video_id}/transcript")
    assert transcript_after.json()["text"] == "edited text\n"


def test_api_batch_transcribe_returns_multiple_tasks(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/tasks/batch",
        json={
            "source_text": "\n".join(
                [
                    "https://www.bilibili.com/video/BV1xx411c7XD",
                    "BV1yy411c7XD",
                ]
            ),
            "provider": "sensevoice",
            "model": "tiny",
            "prompt": "",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert len(payload["items"]) == 2

    for item in payload["items"]:
        task = service.wait_for_task(item["id"])
        assert task.status == "completed"


def test_form_batch_transcribe_renders_batch_page(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/transcribe",
        data={
            "source": "BV1xx411c7XD\nBV1yy411c7XD",
            "provider": "whisper",
            "model": "small",
            "prompt": "",
        },
    )

    assert response.status_code == 200
    assert "已提交 2 个任务" in response.text
    assert "BV1xx411c7XD" in response.text
    assert "BV1yy411c7XD" in response.text
    for task in service.list_tasks():
        service.wait_for_task(task.id)


def test_task_pages_render_the_stored_status_before_polling(tmp_path: Path) -> None:
    app, _, database, _ = build_test_app(tmp_path)
    task = database.create_task(
        kind="transcription",
        source_input="finished.mp4",
        provider="whisper",
        model="small",
    )
    database.complete_task(task.id, message="completed")
    client = TestClient(app)

    task_page = client.get(f"/tasks/{task.id}")
    assert task_page.status_code == 200
    assert "已完成" in task_page.text
    assert 'value="100"' in task_page.text
    assert "completed" in task_page.text

    batch_page = client.get(f"/tasks/batch?ids={task.id}")
    assert batch_page.status_code == 200
    assert "已完成" in batch_page.text
    assert 'value="100"' in batch_page.text


def test_api_supports_categories_tags_and_versions(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "whisper",
            "model": "small",
            "prompt": "",
        },
    ).json()["task_id"]
    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    category = client.post("/api/categories", json={"name": "Research"}).json()
    assert category["name"] == "Research"
    tag = client.post("/api/tags", json={"name": "important"}).json()
    assert tag["name"] == "important"

    assign_category = client.post(
        f"/api/videos/{task.video_id}/category",
        json={"category_id": category["id"]},
    )
    assert assign_category.status_code == 200

    assign_tag = client.post(
        f"/api/videos/{task.video_id}/tags",
        json={"tag_id": tag["id"]},
    )
    assert assign_tag.status_code == 200

    versions = client.get(f"/api/videos/{task.video_id}/versions").json()["items"]
    assert len(versions) == 1

    client.put(f"/api/videos/{task.video_id}/transcript", json={"text": "second version"})
    versions = client.get(f"/api/videos/{task.video_id}/versions").json()["items"]
    assert len(versions) == 2

    activate = client.post(f"/api/videos/{task.video_id}/versions/{versions[-1]['id']}/activate")
    assert activate.status_code == 200


def test_api_exposes_task_events_and_filtered_video_queries(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "whisper",
            "model": "small",
            "prompt": "",
        },
    ).json()["task_id"]
    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    events = client.get(f"/api/tasks/{task_id}/events")
    assert events.status_code == 200
    assert len(events.json()["items"]) >= 2
    assert events.json()["items"][0]["task_id"] == task_id

    category = client.post("/api/categories", json={"name": "Research"}).json()
    tag = client.post("/api/tags", json={"name": "important"}).json()
    client.post(f"/api/videos/{task.video_id}/category", json={"category_id": category["id"]})
    client.post(f"/api/videos/{task.video_id}/tags", json={"tag_id": tag["id"]})

    filtered = client.get(f"/api/videos?query=demo&category_id={category['id']}&tag_id={tag['id']}")
    assert filtered.status_code == 200
    assert len(filtered.json()["items"]) == 1
    assert filtered.json()["items"][0]["id"] == task.video_id


def test_api_supports_category_tag_crud_and_version_detail(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "whisper",
            "model": "small",
            "prompt": "",
        },
    ).json()["task_id"]
    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    category = client.post("/api/categories", json={"name": "Research"}).json()
    updated_category = client.put(f"/api/categories/{category['id']}", json={"name": "Archive"}).json()
    assert updated_category["name"] == "Archive"

    tag = client.post("/api/tags", json={"name": "important"}).json()
    updated_tag = client.put(f"/api/tags/{tag['id']}", json={"name": "featured"}).json()
    assert updated_tag["name"] == "featured"

    client.put(f"/api/videos/{task.video_id}/transcript", json={"text": "edited once"})
    versions = client.get(f"/api/videos/{task.video_id}/versions").json()["items"]
    version_detail = client.get(f"/api/videos/{task.video_id}/versions/{versions[0]['id']}")
    assert version_detail.status_code == 200
    assert "text" in version_detail.json()

    delete_tag = client.delete(f"/api/tags/{updated_tag['id']}")
    assert delete_tag.status_code == 200
    delete_category = client.delete(f"/api/categories/{updated_category['id']}")
    assert delete_category.status_code == 200


def test_api_exposes_task_filters_and_video_metadata(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "sensevoice",
            "model": "tiny",
            "prompt": "",
        },
    ).json()["task_id"]
    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    filtered_tasks = client.get("/api/tasks?status=completed&provider=sensevoice")
    assert filtered_tasks.status_code == 200
    assert len(filtered_tasks.json()["items"]) == 1

    metadata = client.get(f"/api/videos/{task.video_id}/metadata")
    assert metadata.status_code == 200
    assert metadata.json()["engine"] == "sensevoice"
