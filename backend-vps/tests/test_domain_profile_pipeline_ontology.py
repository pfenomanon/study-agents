from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_pipeline_module():
    root = Path(__file__).resolve().parents[1]
    module_path = root / "scripts" / "domain_profile_pipeline.py"
    spec = importlib.util.spec_from_file_location("domain_profile_pipeline", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module spec: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _base_profile() -> dict:
    return {
        "entity_types": [
            "Document",
            "Section",
            "Requirement",
            "RegulatoryBody",
            "Process",
            "Term",
            "Concept",
            "Role",
            "Obligation",
            "Risk",
            "Timeline",
        ],
        "relationship_types": [
            "governs",
            "requires",
            "defines",
            "part_of",
            "references",
            "contradicts",
            "depends_on",
            "enables",
        ],
        "relationship_priorities": [
            "requirements and prerequisites",
            "process ordering and dependencies",
            "authority and governance links",
            "exceptions and contradictions",
        ],
    }


def _source_summaries() -> list[dict]:
    return [
        {
            "source_id": "S1",
            "title": "OpenStack Security Guide",
            "snippet": (
                "Security controls, compliance controls, and tenant isolation for OpenStack "
                "deployments. Controls are approved by control owners and scoped to service boundaries."
            ),
        },
        {
            "source_id": "S2",
            "title": "OpenStack Governance and Risk",
            "snippet": (
                "Risk scenarios, control ownership, audit evidence, and exception workflow. "
                "Findings are evidenced by audit artifacts and owned by governance roles."
            ),
        },
    ]


def test_ontology_quality_rejects_generic_carryover():
    module = _load_pipeline_module()
    base = _base_profile()
    profile = dict(base)

    errors, metrics = module._validate_ontology_quality(
        profile=profile,
        base_profile=base,
        domain="openstack grc security and privacy engineer",
        source_summaries=_source_summaries(),
    )

    assert errors
    assert any("entity_types overlap too much" in e for e in errors)
    assert any("relationship_types overlap too much" in e for e in errors)
    assert any("relationship_priorities remained unchanged" in e for e in errors)
    assert metrics["entity_overlap_ratio"] == 1.0
    assert metrics["relationship_overlap_ratio"] == 1.0


def test_ontology_quality_accepts_domain_native_labels():
    module = _load_pipeline_module()
    base = _base_profile()
    profile = {
        "entity_types": [
            "ControlObjective",
            "SecurityControl",
            "ThreatScenario",
            "AuditEvidence",
            "ExceptionRequest",
            "CloudService",
            "DataClassification",
            "TenantBoundary",
        ],
        "relationship_types": [
            "implements_control",
            "mitigates_risk",
            "evidenced_by",
            "approved_by",
            "owned_by",
            "depends_on",
            "scoped_to_service",
        ],
        "relationship_priorities": [
            "control implementation and evidence linkage in openstack security operations",
            "risk mitigation dependencies across tenant and service boundaries",
            "approval and ownership chains for governance and compliance controls",
            "exceptions and policy conflicts impacting privacy and audit readiness",
        ],
    }

    errors, metrics = module._validate_ontology_quality(
        profile=profile,
        base_profile=base,
        domain="openstack grc security and privacy engineer",
        source_summaries=_source_summaries(),
    )

    assert errors == []
    assert metrics["novel_entity_count"] >= 6
    assert metrics["novel_relationship_count"] >= 4


def test_ontology_quality_rejects_bad_label_format():
    module = _load_pipeline_module()
    base = _base_profile()
    profile = {
        "entity_types": [
            "security control",
            "risk_scenario",
            "AuditEvidence",
            "Role",
            "Concept",
            "Process",
        ],
        "relationship_types": [
            "MitigatesRisk",
            "requires",
            "approved-by",
            "depends_on",
        ],
        "relationship_priorities": [
            "security control dependencies in openstack grc operations",
            "risk mitigation ordering for cloud service controls",
        ],
    }

    errors, _metrics = module._validate_ontology_quality(
        profile=profile,
        base_profile=base,
        domain="openstack grc security and privacy engineer",
        source_summaries=_source_summaries(),
    )

    assert any("entity_types must be PascalCase" in e for e in errors)
    assert any("relationship_types must be snake_case" in e for e in errors)
