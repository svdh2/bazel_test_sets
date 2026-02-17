"""Unit tests for the DAG module."""

from __future__ import annotations

import pytest

from orchestrator.execution.dag import TestDAG, TestNode


# --- Manifest Parsing Tests ---

class TestManifestParsing:
    """Tests for TestDAG.from_manifest()."""

    def test_valid_manifest_two_nodes(self):
        """Parse a valid manifest with two test nodes."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A works", "executable": "/bin/true", "depends_on": []},
                "b": {"assertion": "B works", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        assert len(dag.nodes) == 2
        assert "a" in dag.nodes
        assert "b" in dag.nodes
        assert dag.nodes["a"].assertion == "A works"
        assert dag.nodes["b"].depends_on == ["a"]

    def test_valid_manifest_with_requirement_id(self):
        """Parse manifest with requirement_id field."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": []},
            "test_set_tests": {
                "a": {
                    "assertion": "A works",
                    "executable": "/bin/true",
                    "depends_on": [],
                    "requirement_id": "REQ-001",
                },
            },
        }
        dag = TestDAG.from_manifest(manifest)
        assert dag.nodes["a"].requirement_id == "REQ-001"

    def test_valid_manifest_with_judgement(self):
        """Parse manifest with judgement_executable field."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": []},
            "test_set_tests": {
                "a": {
                    "assertion": "A works",
                    "executable": "/bin/true",
                    "depends_on": [],
                    "judgement_executable": "/path/to/judgement",
                },
            },
        }
        dag = TestDAG.from_manifest(manifest)
        assert dag.nodes["a"].judgement_executable == "/path/to/judgement"

    def test_empty_manifest(self):
        """Parse manifest with no tests."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": [], "subsets": []},
            "test_set_tests": {},
        }
        dag = TestDAG.from_manifest(manifest)
        assert len(dag.nodes) == 0

    def test_missing_test_set_tests(self):
        """Parse manifest without test_set_tests key."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": [], "subsets": []},
        }
        dag = TestDAG.from_manifest(manifest)
        assert len(dag.nodes) == 0

    def test_extra_fields_forward_compat(self):
        """Parse manifest with extra unknown fields (forward compatibility)."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": [], "extra": "ignored"},
            "test_set_tests": {
                "a": {
                    "assertion": "A works",
                    "executable": "/bin/true",
                    "depends_on": [],
                    "future_field": "value",
                },
            },
        }
        dag = TestDAG.from_manifest(manifest)
        assert len(dag.nodes) == 1

    def test_single_node(self):
        """Parse manifest with a single node."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A works", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        assert len(dag.nodes) == 1

    def test_reverse_edges_computed(self):
        """Verify that dependents (reverse edges) are computed correctly."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": []},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["a"]},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        assert sorted(dag.nodes["a"].dependents) == ["b", "c"]
        assert dag.nodes["b"].dependents == []


# --- Topological Sort Tests ---

class TestTopologicalSort:
    """Tests for leaves-first topological sort (diagnostic mode)."""

    def test_linear_chain(self):
        """Linear chain: a -> b -> c. Leaves-first: c, b, a."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["c"]},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        order = dag.topological_sort_leaves_first()
        assert order.index("c") < order.index("b")
        assert order.index("b") < order.index("a")

    def test_diamond(self):
        """Diamond: a depends on b and c, both depend on d."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c", "d"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b", "c"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["d"]},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": ["d"]},
                "d": {"assertion": "D", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        order = dag.topological_sort_leaves_first()
        assert order[0] == "d"  # d is the leaf
        assert order[-1] == "a"  # a is the root
        assert order.index("d") < order.index("b")
        assert order.index("d") < order.index("c")

    def test_forest(self):
        """Forest: two independent chains."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c", "d"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": []},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": ["d"]},
                "d": {"assertion": "D", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        order = dag.topological_sort_leaves_first()
        assert order.index("b") < order.index("a")
        assert order.index("d") < order.index("c")

    def test_single_node(self):
        """Single node with no dependencies."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        order = dag.topological_sort_leaves_first()
        assert order == ["a"]

    def test_empty_dag(self):
        """Empty DAG produces empty order."""
        dag = TestDAG()
        order = dag.topological_sort_leaves_first()
        assert order == []


# --- BFS Ordering Tests ---

class TestBFSOrdering:
    """Tests for roots-first BFS ordering (detection mode)."""

    def test_linear_chain(self):
        """Linear chain: a -> b -> c. Roots-first: a, b, c."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["c"]},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        order = dag.bfs_roots_first()
        assert order.index("a") < order.index("b")
        assert order.index("b") < order.index("c")

    def test_diamond(self):
        """Diamond: a depends on b and c, both depend on d. Root: a first."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c", "d"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b", "c"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["d"]},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": ["d"]},
                "d": {"assertion": "D", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        order = dag.bfs_roots_first()
        assert order[0] == "a"  # a is the root (no dependents)
        assert order[-1] == "d"  # d is the deepest leaf

    def test_forest(self):
        """Forest: multiple roots."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c", "d"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": []},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": ["d"]},
                "d": {"assertion": "D", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        order = dag.bfs_roots_first()
        # Both a and c are roots (no dependents)
        assert order.index("a") < order.index("b")
        assert order.index("c") < order.index("d")

    def test_single_node(self):
        """Single node."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        order = dag.bfs_roots_first()
        assert order == ["a"]

    def test_empty_dag(self):
        """Empty DAG."""
        dag = TestDAG()
        order = dag.bfs_roots_first()
        assert order == []


# --- Cycle Detection Tests ---

class TestCycleDetection:
    """Tests for cycle detection."""

    def test_simple_cycle(self):
        """Two-node cycle: a -> b -> a."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        with pytest.raises(ValueError, match="Cycle detected"):
            dag.topological_sort_leaves_first()

    def test_transitive_cycle(self):
        """Three-node cycle: a -> b -> c -> a."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["c"]},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        with pytest.raises(ValueError, match="Cycle detected"):
            dag.topological_sort_leaves_first()

    def test_self_loop(self):
        """Self-loop: a -> a."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        with pytest.raises(ValueError, match="Cycle detected"):
            dag.topological_sort_leaves_first()

    def test_cycle_in_bfs(self):
        """Cycle also detected in BFS ordering."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        with pytest.raises(ValueError, match="Cycle detected"):
            dag.bfs_roots_first()

    def test_cycle_error_includes_path(self):
        """Cycle error message includes the cycle path."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        with pytest.raises(ValueError) as exc_info:
            dag.topological_sort_leaves_first()
        error_msg = str(exc_info.value)
        assert "a" in error_msg
        assert "b" in error_msg
        assert "->" in error_msg


# --- Helper Method Tests ---

class TestHelperMethods:
    """Tests for get_dependencies and get_dependents."""

    def test_get_dependencies(self):
        """Get direct dependencies of a node."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b", "c"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": []},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        deps = dag.get_dependencies("a")
        assert sorted(deps) == ["b", "c"]
        assert dag.get_dependencies("b") == []

    def test_get_dependencies_nonexistent(self):
        """Get dependencies of a nonexistent node."""
        dag = TestDAG()
        assert dag.get_dependencies("nonexistent") == []

    def test_get_dependents(self):
        """Get nodes that depend on a given node."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": ["b"]},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": []},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": ["b"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        dependents = dag.get_dependents("b")
        assert sorted(dependents) == ["a", "c"]


# --- Disabled Tests ---

class TestDisabled:
    """Tests for the disabled field and remove_disabled()."""

    def test_manifest_with_disabled(self):
        """Parse manifest with disabled=true sets TestNode.disabled."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": [], "disabled": True},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        assert dag.nodes["a"].disabled is True
        assert dag.nodes["b"].disabled is False

    def test_manifest_without_disabled_defaults_false(self):
        """Manifest without disabled key defaults to False."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        assert dag.nodes["a"].disabled is False

    def test_remove_disabled_removes_nodes(self):
        """remove_disabled() removes disabled nodes from DAG."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": [], "disabled": True},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": []},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        removed = dag.remove_disabled()
        assert removed == ["a"]
        assert "a" not in dag.nodes
        assert len(dag.nodes) == 2

    def test_remove_disabled_cleans_edges(self):
        """remove_disabled() cleans up depends_on and dependents edges."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a", "b", "c"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": [], "disabled": True},
                "b": {"assertion": "B", "executable": "/bin/true", "depends_on": ["a"]},
                "c": {"assertion": "C", "executable": "/bin/true", "depends_on": ["a"]},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        dag.remove_disabled()
        assert "a" not in dag.nodes["b"].depends_on
        assert "a" not in dag.nodes["c"].depends_on

    def test_remove_disabled_no_disabled(self):
        """remove_disabled() with no disabled nodes returns empty list."""
        manifest = {
            "test_set": {"name": "root", "assertion": "test", "tests": ["a"], "subsets": []},
            "test_set_tests": {
                "a": {"assertion": "A", "executable": "/bin/true", "depends_on": []},
            },
        }
        dag = TestDAG.from_manifest(manifest)
        removed = dag.remove_disabled()
        assert removed == []
        assert len(dag.nodes) == 1
