import tomllib
from pathlib import Path

from ade_app.provenance import MANIFEST_SCHEMA_VERSION, review_state

ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_is_loopback_only_with_xsrf_protection() -> None:
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

    assert config["server"]["address"] == "127.0.0.1"
    assert config["server"]["enableXsrfProtection"] is True
    launcher = (ROOT / "scripts" / "launch.ps1").read_text(encoding="utf-8")
    assert "--server.address 127.0.0.1" in launcher


def test_document_content_is_not_placed_in_process_wide_streamlit_cache() -> None:
    app_source = (ROOT / "streamlit_app.py").read_text(encoding="utf-8")

    assert "@st.cache_data" not in app_source
    assert "@st.cache_resource" not in app_source


def test_sensitive_evaluation_outputs_are_ignored() -> None:
    ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "evaluation/" in ignore_rules
    assert "tests/" not in ignore_rules


def test_review_state_is_fail_closed() -> None:
    assert review_state(failed_pages=0, unresolved_segments=0) == "not_required"
    assert review_state(failed_pages=0, unresolved_segments=1) == "required_unresolved"
    assert review_state(failed_pages=1, unresolved_segments=0) == "failed"
    assert MANIFEST_SCHEMA_VERSION == 10


def test_private_key_files_are_ignored() -> None:
    ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "*.pem" in ignore_rules
    assert "*.key" in ignore_rules


def test_launcher_refuses_unrelated_port_owners() -> None:
    launcher = (ROOT / "scripts" / "launch.ps1").read_text(encoding="utf-8")

    assert 'Join-Path $venvRoot "Scripts\\python.exe"' in launcher
    assert "$owner.CommandLine.Contains($venvPython)" in launcher
    assert "is owned by unrelated PID" in launcher
    assert "it was not terminated" in launcher


def test_active_prompts_treat_document_instructions_as_untrusted() -> None:
    prompt_dir = ROOT / "src" / "ade_app" / "prompts"

    for name in ("page_extraction.md", "segment_consensus.md", "field_resolution.md"):
        prompt = (prompt_dir / name).read_text(encoding="utf-8").lower()
        assert "untrusted document content" in prompt
        assert "follow" in prompt


def test_governance_evidence_covers_all_required_domains() -> None:
    register = (ROOT / "docs/governance/control-register.md").read_text(encoding="utf-8")
    for domain in (
        "AI system inventory",
        "Data classification and tool boundaries",
        "Acceptable use",
        "Review and validation",
        "Accountable ownership and champions",
        "Role-appropriate training",
        "Monitoring and audit trail",
    ):
        assert domain in register
    assert "compliance certification" in (ROOT / "docs/governance/payer-audit.md").read_text(
        encoding="utf-8"
    )
