from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from codeagent.config import ROOT, SANDBOX_IMAGE, SANDBOX_WORKSPACE_VOLUME
from codeagent.sandbox import SandboxError, _bounded_output, run_fixture_tests, run_python_script


class TestRequest(BaseModel):
    workspace_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$", max_length=100)
    test_path: str | None = Field(default=None, max_length=240)
    test_paths: list[str] | None = Field(default=None, max_length=8)


class ScriptRequest(BaseModel):
    workspace_name: str = Field(pattern=r"^[A-Za-z0-9_.-]+$", max_length=100)
    run_path: str = Field(max_length=240)
    run_args: list[str] = Field(default_factory=list, max_length=8)


class TestResponse(BaseModel):
    returncode: int
    output: str
    stderr: str = ""


app = FastAPI(title="CodeAgent Internal Sandbox", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict[str, str]:
    try:
        import docker

        client = docker.from_env(timeout=3)
        try:
            if not client.ping():
                raise RuntimeError("Docker daemon did not respond.")
            client.images.get(SANDBOX_IMAGE)
            client.volumes.get(SANDBOX_WORKSPACE_VOLUME)
        finally:
            client.close()
    except Exception:
        raise HTTPException(status_code=503, detail="Docker sandbox is not ready.") from None
    return {"status": "ok"}


@app.post("/run-tests", response_model=TestResponse)
def run_tests(request: TestRequest) -> TestResponse:
    workspace = (ROOT / ".runtime" / "jobs" / request.workspace_name).resolve()
    if not workspace.is_dir():
        raise HTTPException(status_code=404, detail="Workspace not found.")
    try:
        result = run_fixture_tests(workspace, request.test_paths or request.test_path)
    except SandboxError as exc:
        raise HTTPException(status_code=503, detail=exc.public_message) from None
    return TestResponse(returncode=result.returncode, output=_bounded_output(result.stdout))


@app.post("/run-script", response_model=TestResponse)
def run_script(request: ScriptRequest) -> TestResponse:
    workspace = (ROOT / ".runtime" / "jobs" / request.workspace_name).resolve()
    if not workspace.is_dir():
        raise HTTPException(status_code=404, detail="Workspace not found.")
    try:
        result = run_python_script(workspace, request.run_path, request.run_args)
    except SandboxError as exc:
        raise HTTPException(status_code=503, detail=exc.public_message) from None
    return TestResponse(returncode=result.returncode, output=_bounded_output(result.stdout),
                        stderr=_bounded_output(result.stderr or ""))
