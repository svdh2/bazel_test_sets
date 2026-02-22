"""Tests for the DAG search index and search box rendering.

Tests cover identifier decomposition, search index construction from
test_set hierarchies (including structured log content), and the
presence of search UI elements in the rendered DAG section.
"""

from __future__ import annotations

import json

from orchestrator.reporting.html_reporter import (
    _build_search_index,
    _decompose_identifier,
    _render_dag_section,
)


class TestDecomposeIdentifier:
    """Tests for _decompose_identifier."""

    def test_camel_case(self):
        result = _decompose_identifier("processPayment")
        assert "process" in result.lower()
        assert "payment" in result.lower()
        assert "processPayment" in result

    def test_snake_case(self):
        result = _decompose_identifier("process_payment")
        assert "process" in result.lower()
        assert "payment" in result.lower()
        assert "process_payment" in result

    def test_consecutive_uppercase(self):
        result = _decompose_identifier("myHTTPClient")
        assert "my" in result.lower()
        assert "http" in result.lower()
        assert "client" in result.lower()
        assert "myHTTPClient" in result

    def test_simple_word(self):
        result = _decompose_identifier("simple")
        assert result == "simple"

    def test_mixed_snake_and_camel(self):
        result = _decompose_identifier("get_userEmail")
        assert "get" in result.lower()
        assert "user" in result.lower()
        assert "email" in result.lower()

    def test_empty_string(self):
        result = _decompose_identifier("")
        assert result == ""


class TestBuildSearchIndex:
    """Tests for _build_search_index."""

    def _make_test_set(self, **overrides):
        base = {
            "name": "root",
            "assertion": "",
            "status": "passed",
            "tests": {},
            "subsets": [],
        }
        base.update(overrides)
        return base

    def test_includes_test_name(self):
        ts = self._make_test_set(tests={
            "//pkg:my_test": {
                "status": "passed", "depends_on": [],
                "assertion": "it works",
            },
        })
        index = _build_search_index(ts)
        assert "//pkg:my_test" in index
        entry = index["//pkg:my_test"]
        assert entry["type"] == "test"
        assert entry["label"] == "my_test"
        assert "my_test" in entry["fields"]["name"]

    def test_includes_assertion(self):
        ts = self._make_test_set(tests={
            "//pkg:t": {
                "status": "passed", "depends_on": [],
                "assertion": "payment succeeds",
            },
        })
        index = _build_search_index(ts)
        assert "payment succeeds" in index["//pkg:t"]["fields"]["assertion"]

    def test_includes_parameters(self):
        ts = self._make_test_set(tests={
            "//pkg:t": {
                "status": "passed", "depends_on": [],
                "parameters": {"region": "us-east-1", "retries": "3"},
            },
        })
        index = _build_search_index(ts)
        fields = index["//pkg:t"]["fields"]
        assert "region" in fields["parameter"]
        assert "us-east-1" in fields["parameter"]
        assert "retries" in fields["parameter"]

    def test_includes_features_from_stdout(self):
        stdout = (
            '[TST] {"type":"block_start","block":"rigging"}\n'
            '[TST] {"type":"feature","name":"authService"}\n'
            '[TST] {"type":"block_end","block":"rigging"}\n'
        )
        ts = self._make_test_set(tests={
            "//pkg:t": {
                "status": "passed", "depends_on": [],
                "stdout": stdout,
            },
        })
        index = _build_search_index(ts)
        fields = index["//pkg:t"]["fields"]
        assert "authservice" in fields["feature"]
        # Decomposed camelCase
        assert "auth" in fields["feature"]
        assert "service" in fields["feature"]

    def test_includes_measurements_from_stdout(self):
        stdout = (
            '[TST] {"type":"block_start","block":"checkpoint"}\n'
            '[TST] {"type":"measurement","name":"responseLatency",'
            '"value":42,"unit":"ms"}\n'
            '[TST] {"type":"block_end","block":"checkpoint"}\n'
        )
        ts = self._make_test_set(tests={
            "//pkg:t": {
                "status": "passed", "depends_on": [],
                "stdout": stdout,
            },
        })
        index = _build_search_index(ts)
        fields = index["//pkg:t"]["fields"]
        assert "responselatency" in fields["metric"]
        assert "response" in fields["metric"]
        assert "latency" in fields["metric"]
        assert "42" in fields["metric"]
        assert "ms" in fields["metric"]

    def test_includes_checks_from_stdout(self):
        stdout = (
            '[TST] {"type":"block_start","block":"verdict"}\n'
            '[TST] {"type":"result","name":"smtpDelivery","passed":true}\n'
            '[TST] {"type":"block_end","block":"verdict"}\n'
        )
        ts = self._make_test_set(tests={
            "//pkg:t": {
                "status": "passed", "depends_on": [],
                "stdout": stdout,
            },
        })
        index = _build_search_index(ts)
        fields = index["//pkg:t"]["fields"]
        assert "smtpdelivery" in fields["check"]
        assert "smtp" in fields["check"]
        assert "delivery" in fields["check"]

    def test_includes_log_content(self):
        ts = self._make_test_set(tests={
            "//pkg:t": {
                "status": "passed", "depends_on": [],
                "stdout": "connecting to server on port 8080",
            },
        })
        index = _build_search_index(ts)
        assert "connecting to server" in index["//pkg:t"]["fields"]["log"]

    def test_log_truncated(self):
        long_stdout = "x" * 10000
        ts = self._make_test_set(tests={
            "//pkg:t": {
                "status": "passed", "depends_on": [],
                "stdout": long_stdout,
            },
        })
        index = _build_search_index(ts)
        assert len(index["//pkg:t"]["fields"]["log"]) == 5000

    def test_includes_set_node(self):
        ts = self._make_test_set(
            name="myTestSet",
            assertion="all pass",
            tests={},
        )
        index = _build_search_index(ts)
        assert "myTestSet" in index
        entry = index["myTestSet"]
        assert entry["type"] == "group"
        assert "mytestset" in entry["fields"]["name"]
        assert "all pass" in entry["fields"]["assertion"]

    def test_excludes_workspace(self):
        ts = self._make_test_set(name="Workspace", subsets=[
            {
                "name": "child_set", "assertion": "", "status": "passed",
                "tests": {}, "subsets": [],
            },
        ])
        index = _build_search_index(ts)
        assert "Workspace" not in index
        assert "child_set" in index

    def test_deduplicates_tests(self):
        test_data = {
            "//pkg:shared": {
                "status": "passed", "depends_on": [],
                "assertion": "works",
            },
        }
        ts = self._make_test_set(
            tests=test_data,
            subsets=[{
                "name": "subset", "assertion": "", "status": "passed",
                "tests": test_data, "subsets": [],
            }],
        )
        index = _build_search_index(ts)
        # Should appear once, not crash or duplicate
        assert "//pkg:shared" in index

    def test_decomposed_identifiers_in_name(self):
        ts = self._make_test_set(tests={
            "//pkg:processPayment": {
                "status": "passed", "depends_on": [],
            },
        })
        index = _build_search_index(ts)
        name_field = index["//pkg:processPayment"]["fields"]["name"]
        assert "process" in name_field
        assert "payment" in name_field

    def test_fields_are_lowercased(self):
        ts = self._make_test_set(tests={
            "//pkg:MyTest": {
                "status": "passed", "depends_on": [],
                "assertion": "SHOULD Pass",
                "parameters": {"Region": "US-East"},
            },
        })
        index = _build_search_index(ts)
        fields = index["//pkg:MyTest"]["fields"]
        assert fields["name"] == fields["name"].lower()
        assert fields["assertion"] == fields["assertion"].lower()
        assert fields["parameter"] == fields["parameter"].lower()

    def test_set_with_steps_in_stdout(self):
        """Steps inside blocks contribute features/metrics/checks."""
        stdout = (
            '[TST] {"type":"block_start","block":"stimulation"}\n'
            '[TST] {"type":"step_start","step":"createOrder",'
            '"description":"Create the order"}\n'
            '[TST] {"type":"measurement","name":"orderTotal",'
            '"value":99.5}\n'
            '[TST] {"type":"result","name":"orderCreated",'
            '"passed":true}\n'
            '[TST] {"type":"step_end","step":"createOrder"}\n'
            '[TST] {"type":"block_end","block":"stimulation"}\n'
        )
        ts = self._make_test_set(tests={
            "//pkg:t": {
                "status": "passed", "depends_on": [],
                "stdout": stdout,
            },
        })
        index = _build_search_index(ts)
        fields = index["//pkg:t"]["fields"]
        # Step name decomposed
        assert "create" in fields["feature"]
        assert "order" in fields["feature"]
        # Measurement from inside the step
        assert "ordertotal" in fields["metric"]
        assert "99.5" in fields["metric"]
        # Check from inside the step
        assert "ordercreated" in fields["check"]


class TestSearchUIRendering:
    """Integration tests for search UI elements in rendered HTML."""

    def test_search_input_in_toolbar(self):
        report = {
            "test_set": {
                "name": "root", "assertion": "", "status": "passed",
                "tests": {}, "subsets": [],
            },
        }
        result = _render_dag_section(report)
        assert 'id="dag-search"' in result
        assert 'id="dag-search-results"' in result
        assert "dag-search-wrapper" in result

    def test_search_index_embedded(self):
        report = {
            "test_set": {
                "name": "root", "assertion": "", "status": "passed",
                "tests": {
                    "//pkg:test_a": {
                        "status": "passed", "depends_on": [],
                        "assertion": "a works",
                    },
                },
                "subsets": [],
            },
        }
        result = _render_dag_section(report)
        assert "var SEARCH_INDEX=" in result
        # Extract the JSON and verify it's valid
        start = result.index("var SEARCH_INDEX=") + len("var SEARCH_INDEX=")
        end = result.index(";</script>", start)
        index_data = json.loads(result[start:end])
        assert "//pkg:test_a" in index_data
        assert index_data["//pkg:test_a"]["type"] == "test"

    def test_search_js_functions_present(self):
        report = {
            "test_set": {
                "name": "root", "assertion": "", "status": "passed",
                "tests": {}, "subsets": [],
            },
        }
        result = _render_dag_section(report)
        assert "performSearch" in result
        assert "selectSearchResult" in result
        assert "parseQuery" in result
