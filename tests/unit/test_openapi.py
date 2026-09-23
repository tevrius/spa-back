from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from openapi_spec_validator import validate

from app.api.documentation import FORECAST_EXAMPLES, MOVEMENT_EXAMPLES
from app.api.schemas import ForecastIn, MovementIn
from app.main import app
from scripts.export_openapi import render_openapi


def test_openapi_is_valid_31_and_snapshot_matches():
    schema = app.openapi()
    assert schema["openapi"] == "3.1.0"
    validate(schema)
    snapshot = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    assert snapshot.read_text(encoding="utf-8") == render_openapi()


def test_every_operation_has_stable_id_description_tags_and_success_example():
    operations = [op for path in app.openapi()["paths"].values() for op in path.values()]
    assert len(operations) == 8
    ids = [op["operationId"] for op in operations]
    assert len(ids) == len(set(ids))
    for operation in operations:
        assert operation["summary"]
        assert operation["description"]
        assert operation["tags"]
        for parameter in operation.get("parameters", []):
            assert parameter["description"]
        for status, response in operation["responses"].items():
            assert response["description"]
            if status.startswith("2"):
                assert response["content"]["application/json"]["example"]


def test_all_response_examples_match_their_json_schema():
    document = app.openapi()
    for path in document["paths"].values():
        for operation in path.values():
            for response in operation["responses"].values():
                media = response["content"]["application/json"]
                schema = {**media["schema"], "components": document["components"]}
                examples = (
                    [media["example"]]
                    if "example" in media
                    else [item["value"] for item in media["examples"].values()]
                )
                for example in examples:
                    Draft202012Validator(
                        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
                    ).validate(example)


def test_all_model_fields_are_described():
    for name, schema in app.openapi()["components"]["schemas"].items():
        for field, definition in schema.get("properties", {}).items():
            assert definition.get("description"), f"Missing documentation: {name}.{field}"


@pytest.mark.parametrize("example", MOVEMENT_EXAMPLES.values())
def test_movement_request_examples_pass_runtime_validation(example):
    MovementIn.model_validate(example["value"])


@pytest.mark.parametrize("example", FORECAST_EXAMPLES.values())
def test_forecast_request_examples_pass_runtime_validation(example):
    ForecastIn.model_validate(example["value"])


@pytest.mark.parametrize(
    "horizon,valid",
    [
        ({"days": 30}, True),
        ({"months": 3}, True),
        ({"days": 30, "months": None}, True),
        ({"days": None, "months": 3}, True),
        ({}, False),
        ({"days": 30, "months": 3}, False),
        ({"days": None, "months": None}, False),
    ],
)
def test_schema_documents_exclusive_forecast_horizons(horizon, valid):
    schema = app.openapi()["components"]["schemas"]["ForecastIn"]
    payload = {"sku": "OIL-001", "location": "MS-01", **horizon}
    assert Draft202012Validator(schema).is_valid(payload) is valid


def test_error_responses_follow_operations_and_custom_error_contract():
    paths = app.openapi()["paths"]
    assert "409" in paths["/api/movements"]["post"]["responses"]
    for path, methods in paths.items():
        for method, operation in methods.items():
            if (path, method) != ("/api/movements", "post"):
                assert "409" not in operation["responses"]
            for status, response in operation["responses"].items():
                if not status.startswith("2"):
                    assert response["content"]["application/json"]["schema"]["$ref"].endswith(
                        "/ErrorResponse"
                    )
