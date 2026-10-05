"""
API Contract Tests

These tests verify that all API routers follow the expected prefix conventions
and are accessible at the correct paths. This prevents regression issues like
the public_skills prefix mismatch that caused 404 errors.

The contract being tested:
- Most routers should use /api/v1/ prefix
- Legacy routers may use /api/ prefix (without v1)
- All routers must be registered in main.py
"""

import pytest

from main import app


class _RouteInfo:
    """Lightweight route view exposing the effective mount path and methods."""

    __slots__ = ("path", "methods")

    def __init__(self, path: str, methods: set[str]):
        self.path = path
        self.methods = methods


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _collect_api_routes() -> list[_RouteInfo]:
    """Return every API route's *effective* path + methods from the OpenAPI schema.

    Reading the OpenAPI schema yields fully-resolved mount paths regardless of how
    FastAPI structures ``app.routes`` internally — flat in older versions, but nested
    ``_IncludedRouter`` wrappers in 0.138+ (whose ``original_router.routes`` still
    carry pre-prefix paths). Walking those wrappers directly would report un-mounted
    paths (e.g. ``/skills/pending`` instead of ``/api/admin/skills/pending``); the
    schema avoids that entirely.
    """
    schema = app.openapi()
    routes: list[_RouteInfo] = []
    for path, operations in schema.get("paths", {}).items():
        methods = {m.upper() for m in operations if m.lower() in _HTTP_METHODS}
        routes.append(_RouteInfo(path, methods))
    return routes


class TestRouterPrefixContract:
    """Test that all routers follow the API prefix contract."""

    # Routers that are expected to use /api/v1/ prefix
    EXPECTED_V1_PREFIX_ROUTERS = {
        "/api/v1/public-skills",  # Public skill discovery
        "/api/v1/skills",         # User skills
        "/api/v1/inspirations",   # Inspirations
        "/api/v1/materials",      # Materials
        "/api/v1/points",         # Points system
        "/api/v1/referral",       # Referral system
        "/api/v1/agent-api-keys", # Agent API keys
        "/api/v1/agent",          # Agent API
    }

    # Routers that use /api/v1 as base prefix (with nested paths in route decorators)
    EXPECTED_V1_BASE_ROUTERS = {
        "/api/v1",  # files, projects, export (these have nested paths)
    }

    # Routers that use /api/ prefix without v1 (legacy/intentional)
    EXPECTED_API_PREFIX_ROUTERS = {
        "/api/auth",   # Authentication
        "/api/admin",  # Admin panel
    }

    # Routers that use custom prefixes (not /api/v1 or /api)
    CUSTOM_PREFIX_ROUTERS = {
        "/agent",  # Agent SSE streaming
        "/chat",   # Chat API
    }

    # All expected prefixes combined
    ALL_EXPECTED_PREFIXES = (
        EXPECTED_V1_PREFIX_ROUTERS |
        EXPECTED_V1_BASE_ROUTERS |
        EXPECTED_API_PREFIX_ROUTERS |
        CUSTOM_PREFIX_ROUTERS
    )

    def test_public_skills_uses_v1_prefix(self):
        """Verify public_skills router uses /api/v1/public-skills prefix."""
        routes = self._get_all_routes()
        public_skills_routes = [r for r in routes if "public-skills" in r.path]

        assert len(public_skills_routes) > 0, (
            "No routes found for public-skills. "
            "Router may not be registered or prefix is wrong."
        )

        for route in public_skills_routes:
            assert route.path.startswith("/api/v1/public-skills"), (
                f"Route {route.path} does not use /api/v1/public-skills prefix. "
                "This will cause 404 errors when frontend calls the API."
            )

    def test_skills_uses_v1_prefix(self):
        """Verify skills router uses /api/v1/skills prefix."""
        routes = self._get_all_routes()
        skills_routes = [r for r in routes if r.path.startswith("/api/v1/skills")]

        assert len(skills_routes) > 0, (
            "No routes found with /api/v1/skills prefix. "
            "Skills router may not be registered correctly."
        )

    def test_inspirations_uses_v1_prefix(self):
        """Verify inspirations router uses /api/v1/inspirations prefix."""
        routes = self._get_all_routes()
        inspirations_routes = [r for r in routes if r.path.startswith("/api/v1/inspirations")]

        assert len(inspirations_routes) > 0, (
            "No routes found with /api/v1/inspirations prefix."
        )

    def test_materials_uses_v1_prefix(self):
        """Verify materials router uses /api/v1/materials prefix."""
        routes = self._get_all_routes()
        materials_routes = [r for r in routes if r.path.startswith("/api/v1/materials")]

        assert len(materials_routes) > 0, (
            "No routes found with /api/v1/materials prefix."
        )

    def test_referral_uses_v1_prefix(self):
        """Verify referral router uses /api/v1/referral prefix."""
        routes = self._get_all_routes()
        referral_routes = [r for r in routes if r.path.startswith("/api/v1/referral")]

        assert len(referral_routes) > 0, (
            "No routes found with /api/v1/referral prefix."
        )

    def test_agent_api_keys_uses_v1_prefix(self):
        """Verify agent_api_keys router uses /api/v1/agent-api-keys prefix."""
        routes = self._get_all_routes()
        agent_keys_routes = [r for r in routes if r.path.startswith("/api/v1/agent-api-keys")]

        assert len(agent_keys_routes) > 0, (
            "No routes found with /api/v1/agent-api-keys prefix."
        )

    def test_auth_uses_api_prefix(self):
        """Verify auth router uses /api/auth prefix (legacy convention)."""
        routes = self._get_all_routes()
        auth_routes = [r for r in routes if r.path.startswith("/api/auth")]

        assert len(auth_routes) > 0, (
            "No routes found with /api/auth prefix."
        )

    def test_admin_uses_api_prefix(self):
        """Verify admin router uses /api/admin prefix."""
        routes = self._get_all_routes()
        admin_routes = [r for r in routes if r.path.startswith("/api/admin")]

        assert len(admin_routes) > 0, (
            "No routes found with /api/admin prefix."
        )

    def test_no_routes_with_bare_resource_prefix(self):
        """
        Verify no routes use bare resource prefixes like /public-skills.

        This was the bug: router used /public-skills instead of /api/v1/public-skills.
        This test ensures such regression doesn't happen again.
        """
        routes = self._get_all_routes()

        # List of bare prefixes that should NOT exist
        # (they should have /api/v1 or /api prefix)
        forbidden_bare_prefixes = [
            "/public-skills",
            "/skills",
            "/inspirations",
            "/materials",
            "/points",
            "/referral",
        ]

        for route in routes:
            for forbidden_prefix in forbidden_bare_prefixes:
                if route.path == forbidden_prefix or route.path.startswith(f"{forbidden_prefix}/"):
                    pytest.fail(
                        f"Route {route.path} uses bare prefix '{forbidden_prefix}'. "
                        f"All API routes should use /api/v1/ or /api/ prefix. "
                        f"This causes 404 errors when frontend expects /api/v1{forbidden_prefix}."
                    )

    def test_all_routes_have_valid_prefix(self):
        """
        Verify all routes have a valid prefix.

        Valid prefixes are:
        - /api/v1/* (versioned API)
        - /api/* (legacy API)
        - /agent (SSE streaming)
        - /chat (Chat API)
        - / (root endpoints like /health, /skill.md)
        """
        routes = self._get_all_routes()

        # Root-level endpoints that are intentionally at /
        allowed_root_endpoints = {"/", "/health", "/health/ready", "/skill.md", "/docs", "/openapi.json"}

        for route in routes:
            # Skip allowed root endpoints
            if route.path in allowed_root_endpoints:
                continue

            # Check if route has a valid prefix
            has_valid_prefix = (
                route.path.startswith("/api/v1/") or
                route.path.startswith("/api/") or
                route.path.startswith("/agent") or
                route.path.startswith("/chat")
            )

            if not has_valid_prefix:
                pytest.fail(
                    f"Route {route.path} does not have a valid prefix. "
                    f"Valid prefixes: /api/v1/, /api/, /agent, /chat. "
                    f"Root endpoints {allowed_root_endpoints} are also allowed."
                )

    def test_v1_base_routes_exist(self):
        """
        Verify routes that use /api/v1 base prefix with nested paths exist.

        These routers use prefix="/api/v1" and define paths like /projects, /files, etc.
        """
        routes = self._get_all_routes()

        # Routes that should exist under /api/v1
        expected_v1_paths = [
            "/projects",
            "/files",
        ]

        for expected_path in expected_v1_paths:
            matching_routes = [
                r for r in routes
                if r.path == f"/api/v1{expected_path}" or
                   r.path.startswith(f"/api/v1{expected_path}/")
            ]

            assert len(matching_routes) > 0, (
                f"No routes found for /api/v1{expected_path}. "
                f"Router may not be registered correctly."
            )

    def test_route_methods_are_defined(self):
        """Verify all routes have at least one HTTP method defined."""
        routes = self._get_all_routes()

        for route in routes:
            assert len(route.methods) > 0, (
                f"Route {route.path} has no HTTP methods defined."
            )

    def test_critical_routes_accessible(self):
        """
        Verify critical business routes are registered and accessible.

        This test ensures the most important routes for the application exist.
        """
        routes = self._get_all_routes()
        route_paths = {r.path for r in routes}

        # Critical routes that must exist
        critical_routes = [
            "/api/v1/public-skills/categories",
            "/api/v1/skills",
            "/api/v1/materials",
        ]

        for critical_route in critical_routes:
            # Check if the route or a variation exists
            route_exists = any(
                path == critical_route or path.startswith(f"{critical_route}/")
                for path in route_paths
            )

            # For parameterized routes, check prefix
            if not route_exists:
                base_path = "/".join(critical_route.split("/")[:-1]) if "{" in critical_route else critical_route
                route_exists = any(
                    path.startswith(base_path) for path in route_paths
                )

            assert route_exists, (
                f"Critical route {critical_route} not found in registered routes. "
                f"This may indicate a router registration issue."
            )

    # Helper methods

    def _get_all_routes(self) -> list[_RouteInfo]:
        """Get all API routes (effective paths) from the FastAPI app."""
        return _collect_api_routes()


class TestRouterPrefixPatterns:
    """Test router prefix patterns for consistency."""

    def test_v1_routers_use_kebab_case(self):
        """
        Verify /api/v1 routers use kebab-case for multi-word resources.

        Example: /api/v1/public-skills, /api/v1/agent-api-keys
        """
        routes = TestRouterPrefixContract()._get_all_routes()

        # Routers that correctly use kebab-case in v1 API
        kebab_case_v1_routes = [
            r for r in routes
            if r.path.startswith("/api/v1/") and "-" in r.path
        ]

        # Verify we have some kebab-case routes (proves the pattern is used)
        assert len(kebab_case_v1_routes) > 0, (
            "No kebab-case routes found in /api/v1. "
            "Multi-word resources should use kebab-case (e.g., /public-skills)."
        )

    def test_no_snake_case_in_routes(self):
        """
        Verify routes don't use snake_case (should use kebab-case).

        Exception: Some legacy routes may use snake_case.
        """
        routes = TestRouterPrefixContract()._get_all_routes()

        # Routes that are exempt from this check (legacy)
        exempt_prefixes = ["/api/auth", "/agent", "/chat"]

        for route in routes:
            # Skip exempt routes
            if any(route.path.startswith(prefix) for prefix in exempt_prefixes):
                continue

            # Check for snake_case in path segments (excluding path parameters)
            path_segments = [
                seg for seg in route.path.split("/")
                if seg and not seg.startswith("{")
            ]

            for segment in path_segments:
                if "_" in segment:
                    pytest.fail(
                        f"Route {route.path} uses snake_case segment '{segment}'. "
                        f"REST API paths should use kebab-case (e.g., /public-skills not /public_skills)."
                    )


class TestPublicSkillsEndpointContract:
    """
    Specific contract tests for public_skills endpoints.

    These tests ensure the public_skills router is correctly configured
    and accessible at the expected paths.
    """

    def test_public_skills_categories_endpoint_exists(self):
        """Verify /api/v1/public-skills/categories endpoint exists."""
        routes = TestRouterPrefixContract()._get_all_routes()
        categories_route = next(
            (r for r in routes if r.path == "/api/v1/public-skills/categories"),
            None
        )

        assert categories_route is not None, (
            "Endpoint /api/v1/public-skills/categories not found. "
            "This endpoint is required for the frontend category filter."
        )

        assert "GET" in categories_route.methods, (
            "Endpoint /api/v1/public-skills/categories should support GET method."
        )

    def test_public_skills_list_endpoint_exists(self):
        """Verify /api/v1/public-skills list endpoint exists."""
        routes = TestRouterPrefixContract()._get_all_routes()

        # List endpoint could be /api/v1/public-skills or /api/v1/public-skills/
        list_routes = [
            r for r in routes
            if r.path in ["/api/v1/public-skills", "/api/v1/public-skills/"]
        ]

        assert len(list_routes) > 0, (
            "No list endpoint found for /api/v1/public-skills. "
            "This endpoint is required for listing public skills."
        )

        # Verify GET method exists
        get_route = next((r for r in list_routes if "GET" in r.methods), None)
        assert get_route is not None, (
            "List endpoint /api/v1/public-skills should support GET method."
        )

    def test_public_skills_detail_endpoint_exists(self):
        """Verify /api/v1/public-skills/{id} detail endpoint exists."""
        routes = TestRouterPrefixContract()._get_all_routes()

        detail_route = next(
            (r for r in routes if r.path == "/api/v1/public-skills/{skill_id}"),
            None
        )

        assert detail_route is not None, (
            "Detail endpoint /api/v1/public-skills/{skill_id} not found. "
            "This endpoint is required for viewing skill details."
        )

        assert "GET" in detail_route.methods, (
            "Detail endpoint should support GET method."
        )
