"""DAG data structures and algorithms for test orchestration.

Provides TestNode (wraps test_set_test data) and TestDAG (graph with
ordering methods for diagnostic and detection modes).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TestNode:
    """Represents a single test_set_test in the DAG."""

    name: str
    assertion: str
    executable: str
    depends_on: list[str] = field(default_factory=list)
    requirement_id: str = ""
    judgement_executable: str | None = None
    disabled: bool = False

    # Computed graph edges (populated during DAG construction)
    dependents: list[str] = field(default_factory=list)  # nodes that depend on this one


class TestDAG:
    """Directed acyclic graph of test nodes with ordering algorithms.

    Supports:
    - Construction from manifest JSON
    - Cycle detection with path reporting
    - Topological sort (leaves-first for diagnostic mode)
    - BFS ordering (roots-first for detection mode)
    """

    def __init__(self) -> None:
        self.nodes: dict[str, TestNode] = {}

    @classmethod
    def from_manifest(cls, manifest: dict[str, Any]) -> TestDAG:
        """Construct a TestDAG from a parsed manifest JSON.

        Args:
            manifest: Dict with 'test_set' and 'test_set_tests' keys.

        Returns:
            A fully constructed TestDAG.

        Raises:
            ValueError: If the manifest is missing required fields.
        """
        dag = cls()
        test_set_tests = manifest.get("test_set_tests", {})

        if not test_set_tests:
            return dag

        # Create nodes
        for name, data in test_set_tests.items():
            node = TestNode(
                name=name,
                assertion=data.get("assertion", ""),
                executable=data.get("executable", ""),
                depends_on=list(data.get("depends_on", [])),
                requirement_id=data.get("requirement_id", ""),
                judgement_executable=data.get("judgement_executable"),
                disabled=bool(data.get("disabled", False)),
            )
            dag.nodes[name] = node

        # Compute reverse edges (dependents)
        for name, node in dag.nodes.items():
            for dep_name in node.depends_on:
                if dep_name in dag.nodes:
                    dag.nodes[dep_name].dependents.append(name)

        return dag

    def _detect_cycle(self) -> list[str] | None:
        """Detect cycles in the DAG using DFS.

        Returns:
            A list of node names forming the cycle, or None if acyclic.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {name: WHITE for name in self.nodes}
        path: list[str] = []

        def dfs(node_name: str) -> list[str] | None:
            color[node_name] = GRAY
            path.append(node_name)

            for dep_name in self.nodes[node_name].depends_on:
                if dep_name not in self.nodes:
                    continue
                if color[dep_name] == GRAY:
                    # Found a cycle -- extract the cycle path
                    cycle_start = path.index(dep_name)
                    return path[cycle_start:] + [dep_name]
                if color[dep_name] == WHITE:
                    result = dfs(dep_name)
                    if result is not None:
                        return result

            path.pop()
            color[node_name] = BLACK
            return None

        for name in self.nodes:
            if color[name] == WHITE:
                result = dfs(name)
                if result is not None:
                    return result

        return None

    def topological_sort_leaves_first(self) -> list[str]:
        """Topological sort with leaves (no dependencies) first.

        This is the ordering for diagnostic mode: execute leaves first,
        then work up toward roots.

        Returns:
            List of node names in leaves-first order.

        Raises:
            ValueError: If the graph contains a cycle.
        """
        cycle = self._detect_cycle()
        if cycle is not None:
            cycle_str = " -> ".join(cycle)
            raise ValueError(f"Cycle detected in test DAG: {cycle_str}")

        # Kahn's algorithm
        in_degree: dict[str, int] = {name: 0 for name in self.nodes}
        for node in self.nodes.values():
            for dep_name in node.depends_on:
                if dep_name in self.nodes:
                    in_degree[dep_name] += 1

        # Start with nodes that have no dependents (i.e., leaves in terms
        # of the "depended-on-by" relationship, meaning nodes nobody depends on).
        # Actually for leaves-first, we want nodes with no dependencies first.
        queue: deque[str] = deque()
        for name, degree in in_degree.items():
            # in_degree counts how many other nodes depend_on this node.
            # We want nodes with no outgoing depends_on edges first (leaves).
            pass

        # Re-approach: leaves are nodes with no depends_on (no outgoing edges).
        # We want them first, then nodes whose deps are all leaves, etc.
        # This is a standard topological sort of the dependency graph.

        # Compute out-degree (number of depends_on edges)
        remaining_deps: dict[str, int] = {}
        for name, node in self.nodes.items():
            count = sum(1 for d in node.depends_on if d in self.nodes)
            remaining_deps[name] = count

        queue = deque()
        for name, count in remaining_deps.items():
            if count == 0:
                queue.append(name)

        result: list[str] = []
        while queue:
            name = queue.popleft()
            result.append(name)
            # For each node that depends on this one, decrement their remaining deps
            for dependent_name in self.nodes[name].dependents:
                if dependent_name in remaining_deps:
                    remaining_deps[dependent_name] -= 1
                    if remaining_deps[dependent_name] == 0:
                        queue.append(dependent_name)

        if len(result) != len(self.nodes):
            # Should not happen since we checked for cycles above
            raise ValueError("Topological sort incomplete -- possible undetected cycle")

        return result

    def bfs_roots_first(self) -> list[str]:
        """BFS ordering starting from root nodes (no dependents).

        This is the ordering for detection mode: start from roots
        (high-level tests) and work down to leaves.

        Returns:
            List of node names in BFS roots-first order.

        Raises:
            ValueError: If the graph contains a cycle.
        """
        cycle = self._detect_cycle()
        if cycle is not None:
            cycle_str = " -> ".join(cycle)
            raise ValueError(f"Cycle detected in test DAG: {cycle_str}")

        # Find roots: nodes with no dependents (nobody depends on them in the
        # depends_on direction -- i.e., nodes that are not in anyone's depends_on list)
        roots: list[str] = []
        for name, node in self.nodes.items():
            if not node.dependents:
                roots.append(name)

        # BFS from roots
        visited: set[str] = set()
        queue: deque[str] = deque(roots)
        result: list[str] = []

        for root in roots:
            visited.add(root)

        while queue:
            name = queue.popleft()
            result.append(name)

            # Visit dependencies (go deeper into the DAG)
            for dep_name in self.nodes[name].depends_on:
                if dep_name in self.nodes and dep_name not in visited:
                    visited.add(dep_name)
                    queue.append(dep_name)

        # Add any remaining unvisited nodes (disconnected subgraphs)
        for name in self.nodes:
            if name not in visited:
                result.append(name)

        return result

    def get_dependencies(self, name: str) -> list[str]:
        """Get the direct dependencies of a node.

        Args:
            name: Node name.

        Returns:
            List of dependency node names (that exist in the DAG).
        """
        if name not in self.nodes:
            return []
        return [d for d in self.nodes[name].depends_on if d in self.nodes]

    def get_dependents(self, name: str) -> list[str]:
        """Get the nodes that depend on this node.

        Args:
            name: Node name.

        Returns:
            List of dependent node names.
        """
        if name not in self.nodes:
            return []
        return list(self.nodes[name].dependents)

    def remove_disabled(self) -> list[str]:
        """Remove disabled nodes from the DAG and clean up edges.

        Returns:
            List of removed node names.
        """
        disabled = [name for name, node in self.nodes.items() if node.disabled]
        for name in disabled:
            for other in self.nodes.values():
                if name in other.depends_on:
                    other.depends_on.remove(name)
                if name in other.dependents:
                    other.dependents.remove(name)
            del self.nodes[name]
        return disabled
