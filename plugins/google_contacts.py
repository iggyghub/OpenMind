"""
Google Contacts MCP plugin -- Issue #229, ADR-0005.

Four tools, hitting the **real Google People API**, authorized by the active
profile's OAuth **access token** read from the #112 credential store
(refreshed on demand via #113):

  - ``contacts_search``       -- People API ``people.search`` (read-only).
  - ``contacts_get``          -- People API ``people.get`` (read-only).
  - ``contacts_create``       -- People API ``people.createContact`` (write,
                                 ask-class ``external_data_write``).
  - ``contacts_update``       -- People API ``people.updateContact`` (write,
                                 ask-class ``external_data_write``).

The bearer token reaches the active profile via a module-level
``set_token_provider`` setter wired from ``cerebral/main.py`` -- the exact
``plugins/gmail.py`` / ``plugins/calendar.py`` / ``plugins/google_tasks.py``
precedent. Tests bypass it by passing a stub provider + stub ``fetch_fn`` to
the constructor: no real network, OAuth, keyring or browser in the suite.

**No live-verify in this slice.** Live verification is batched into slice
V.1 (#239) so the autonomous loop is never blocked on browser OAuth consent.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from cerebral.mcp.orchestrator import Tool, ToolResult

logger = logging.getLogger(__name__)

PLUGIN_NAME = "google_contacts"

# ADR-0005 / Issue #229.
#   - external_data_read + network_egress_cloud: contacts_search and
#     contacts_get fetch data from people.googleapis.com over the internet
#     (the gmail.py / calendar.py surface).
#   - secrets_read is a DELIBERATE over-declaration -- the gmail.py /
#     calendar.py posture-B precedent (clone it, do NOT contrast it).
#   - external_data_write (contacts_create, contacts_update): these tools
#     mutate an external account (the active profile's contacts). Hand-declared:
#     external_data_* is absent from the AST capability map AND the bare-attr
#     fallback.
REQUIRED_CAPABILITIES: frozenset[str] = frozenset({
    "secrets_read",
    "external_data_read",
    "external_data_write",
    "network_egress_cloud",
})

_BASE = "https://people.googleapis.com/v1"
_DEFAULT_LIMIT = 10

_FACTORY_NOT_WIRED_MSG = "Google Contacts is not available -- token provider not wired"
_NO_ACCOUNT_MSG = "no Google account connected"
_MISSING_SEARCH_ARGS_MSG = "missing required arg(s) for contacts_search: {}"
_MISSING_GET_ARGS_MSG = "missing required arg(s) for contacts_get: {}"
_MISSING_CREATE_ARGS_MSG = "missing required arg(s) for contacts_create: {}"
_MISSING_UPDATE_ARGS_MSG = "missing required arg(s) for contacts_update: {}"


class TokenProvider(Protocol):
    """Per-active-profile bearer-token handle wired from main.py.

    ``current()`` returns the stored access token (no network) or ``None``;
    ``refresh()`` exchanges the stored refresh token for a fresh access
    token via #113 (the 401 path) and returns it.
    """

    def current(self) -> Optional[str]: ...
    def refresh(self) -> str: ...


TokenProviderFactory = Callable[[], Optional[TokenProvider]]

_token_provider_factory: Optional[TokenProviderFactory] = None


def set_token_provider(fn: TokenProviderFactory) -> None:
    """Wire main.py's _get_google_contacts_token_provider() -- called once at
    startup after the orchestrator has discovered the plugin."""
    global _token_provider_factory
    _token_provider_factory = fn


class ContactsAPIError(RuntimeError):
    """Any transport/HTTP failure of a Contacts call. ``status`` is the HTTP
    status code when available (so the 401 -> refresh -> retry path can
    branch on it), else ``None``."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


FetchFn = Callable[..., Awaitable[Any]]


async def _default_fetch(method: str, url: str, *, headers: dict | None = None,
                         params: dict | None = None,
                         json: dict | None = None) -> Any:
    """Default transport: aiohttp -> httpx fallback. Returns parsed JSON.

    HTTP errors are mapped to ContactsAPIError carrying the status code so the
    caller can branch on 401; transport/other errors carry status=None.
    Deps lazy-imported here so module import stays stdlib-only.
    """
    try:
        import aiohttp  # type: ignore
    except ImportError:
        aiohttp = None  # type: ignore
    if aiohttp is not None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, headers=headers,
                                            params=params, json=json) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        except aiohttp.ClientResponseError as exc:  # type: ignore[attr-defined]
            raise ContactsAPIError(str(exc), status=exc.status) from exc
        except ContactsAPIError:
            raise
        except Exception as exc:
            raise ContactsAPIError(str(exc), status=None) from exc

    try:
        import httpx  # type: ignore
    except ImportError:
        httpx = None  # type: ignore
    if httpx is not None:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(method, url, headers=headers,
                                            params=params, json=json)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:  # type: ignore[attr-defined]
            raise ContactsAPIError(
                str(exc), status=exc.response.status_code
            ) from exc
        except ContactsAPIError:
            raise
        except Exception as exc:
            raise ContactsAPIError(str(exc), status=None) from exc

    raise ContactsAPIError(
        "Neither aiohttp nor httpx is installed -- cannot make HTTP requests",
        status=None,
    )


def _shape_contact(contact: Any) -> dict:
    """Flatten one contact response row."""
    if not isinstance(contact, dict):
        return {}
    names = contact.get("names", [])
    display_name = ""
    if isinstance(names, list) and len(names) > 0:
        name_obj = names[0]
        if isinstance(name_obj, dict):
            display_name = (name_obj.get("displayName") or
                          " ".join(filter(None, [
                              name_obj.get("givenName", ""),
                              name_obj.get("familyName", "")
                          ])))
    emails = contact.get("emailAddresses", [])
    email_list = []
    if isinstance(emails, list):
        email_list = [e.get("value", "") for e in emails if isinstance(e, dict)]
    return {
        "resource_name": contact.get("resourceName", ""),
        "display_name": display_name,
        "emails": email_list[:3],
    }


class GoogleContactsPlugin:
    name = PLUGIN_NAME

    def __init__(self, token_provider: Optional[TokenProvider] = None,
                 fetch_fn: FetchFn | None = None) -> None:
        self._provider = token_provider
        self._fetch = fetch_fn or _default_fetch
        self._seen_tokens: set[str] = set()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name="contacts_search",
                description=(
                    "Search contacts in the active profile's Google Contacts "
                    "by name or email. Returns resource name, display name, "
                    "and email addresses."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Search query (name or email to search for)."
                            ),
                        },
                        "max_results": {
                            "type": "integer",
                            "description": (
                                f"Maximum contacts to return (default "
                                f"{_DEFAULT_LIMIT})."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="contacts_get",
                description=(
                    "Get detailed information for a specific contact by "
                    "resource name."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "resource_name": {
                            "type": "string",
                            "description": "The contact resource name.",
                        },
                    },
                    "required": ["resource_name"],
                },
            ),
            Tool(
                name="contacts_create",
                description=(
                    "Create a new contact in the active profile's Google "
                    "Contacts via the real People API."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "display_name": {
                            "type": "string",
                            "description": "Contact display name.",
                        },
                        "email": {
                            "type": "string",
                            "description": "Contact email address (optional).",
                        },
                        "phone": {
                            "type": "string",
                            "description": (
                                "Contact phone number in E.164 format "
                                "(optional)."
                            ),
                        },
                    },
                    "required": ["display_name"],
                },
            ),
            Tool(
                name="contacts_update",
                description=(
                    "Update an existing contact in the active profile's Google "
                    "Contacts."
                ),
                plugin=PLUGIN_NAME,
                schema={
                    "type": "object",
                    "properties": {
                        "resource_name": {
                            "type": "string",
                            "description": "The contact resource name to update.",
                        },
                        "display_name": {
                            "type": "string",
                            "description": "New display name (optional).",
                        },
                        "email": {
                            "type": "string",
                            "description": "New email address (optional).",
                        },
                        "phone": {
                            "type": "string",
                            "description": (
                                "New phone number in E.164 format (optional)."
                            ),
                        },
                    },
                    "required": ["resource_name"],
                },
            ),
        ]

    async def call_tool(self, tool_name: str, args: dict) -> ToolResult:
        if tool_name == "contacts_search":
            return await self._search(args)
        if tool_name == "contacts_get":
            return await self._get(args)
        if tool_name == "contacts_create":
            return await self._create(args)
        if tool_name == "contacts_update":
            return await self._update(args)
        return ToolResult(content=f"Unknown tool: '{tool_name}'", is_error=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_provider(self) -> tuple[Optional[TokenProvider], Optional[str]]:
        """Return ``(provider, error_string)``. Exactly one is non-None."""
        factory = self._token_factory()
        if self._provider is not None:
            return self._provider, None
        if factory is None:
            return None, _FACTORY_NOT_WIRED_MSG
        provider = factory()
        if provider is None:
            return None, _NO_ACCOUNT_MSG
        return provider, None

    @staticmethod
    def _token_factory() -> Optional[TokenProviderFactory]:
        return _token_provider_factory

    def _scrub(self, text: str) -> str:
        """Strip every bearer token seen this call from text bound for a log
        or ToolResult."""
        for tok in self._seen_tokens:
            if tok:
                text = text.replace(tok, "***")
        return text

    def _take_token(self, provider: TokenProvider, *, force: bool) -> str:
        """Get a bearer token: refresh on ``force`` (the 401 path) or when
        none is stored, else the stored token. Records it for scrubbing."""
        token = None if force else provider.current()
        if not token:
            token = provider.refresh()
        if token:
            self._seen_tokens.add(token)
        return token or ""

    async def _search_contacts(self, token: str,
                               params: dict | None = None) -> Any:
        return await self._fetch(
            "GET",
            f"{_BASE}/people:search",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _get_contact(self, token: str, resource_name: str,
                           params: dict | None = None) -> Any:
        return await self._fetch(
            "GET",
            f"{_BASE}/{resource_name}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    async def _create_contact(self, token: str, body: dict) -> Any:
        return await self._fetch(
            "POST",
            f"{_BASE}/people:createContact",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    async def _update_contact(self, token: str, resource_name: str,
                              body: dict) -> Any:
        return await self._fetch(
            "PATCH",
            f"{_BASE}/{resource_name}:updateContact",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )

    async def _search(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query or not isinstance(query, str):
            return ToolResult(
                content=_MISSING_SEARCH_ARGS_MSG.format("query"),
                is_error=True,
            )

        max_results = int(args.get("max_results") or _DEFAULT_LIMIT)
        params = {"query": query, "pageSize": max_results}

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                listing = await self._search_contacts(token, params=params)
            except ContactsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                listing = await self._search_contacts(token, params=params)
        except Exception as exc:
            logger.error(
                "[google_contacts] contacts_search failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Contacts search failed: {exc}"),
                is_error=True,
            )

        if not isinstance(listing, dict):
            return ToolResult(
                content="unexpected Contacts search response", is_error=True
            )
        items = [
            c for c in (listing.get("results") or [])
            if isinstance(c, dict)
        ][:max_results]
        return ToolResult(content=json.dumps({"contacts": [
            _shape_contact(c.get("person", {})) for c in items
        ]}))

    async def _get(self, args: dict) -> ToolResult:
        resource_name = args.get("resource_name")
        if not resource_name or not isinstance(resource_name, str):
            return ToolResult(
                content=_MISSING_GET_ARGS_MSG.format("resource_name"),
                is_error=True,
            )

        params = {"personFields": "names,emailAddresses,phoneNumbers"}

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                contact = await self._get_contact(token, resource_name,
                                                  params=params)
            except ContactsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                contact = await self._get_contact(token, resource_name,
                                                  params=params)
        except Exception as exc:
            logger.error(
                "[google_contacts] contacts_get failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Contacts get failed: {exc}"),
                is_error=True,
            )

        if not isinstance(contact, dict):
            return ToolResult(
                content="unexpected Contacts get response", is_error=True
            )
        return ToolResult(content=json.dumps(_shape_contact(contact)))

    async def _create(self, args: dict) -> ToolResult:
        display_name = args.get("display_name")
        if not display_name or not isinstance(display_name, str):
            return ToolResult(
                content=_MISSING_CREATE_ARGS_MSG.format("display_name"),
                is_error=True,
            )

        body: dict = {
            "names": [{"givenName": display_name}]
        }
        email = args.get("email")
        if isinstance(email, str) and email:
            body["emailAddresses"] = [{"value": email}]
        phone = args.get("phone")
        if isinstance(phone, str) and phone:
            body["phoneNumbers"] = [{"value": phone}]

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._create_contact(token, body)
            except ContactsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._create_contact(token, body)
        except Exception as exc:
            logger.error(
                "[google_contacts] contacts_create failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Contacts create failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Contacts create response", is_error=True
            )
        return ToolResult(content=json.dumps({
            "resource_name": resp.get("resourceName", ""),
            "display_name": display_name,
            "status": "created",
        }))

    async def _update(self, args: dict) -> ToolResult:
        resource_name = args.get("resource_name")
        if not resource_name or not isinstance(resource_name, str):
            return ToolResult(
                content=_MISSING_UPDATE_ARGS_MSG.format("resource_name"),
                is_error=True,
            )

        body: dict = {"names": [{}]}
        display_name = args.get("display_name")
        if isinstance(display_name, str) and display_name:
            body["names"] = [{"givenName": display_name}]
        email = args.get("email")
        if isinstance(email, str) and email:
            body["emailAddresses"] = [{"value": email}]
        phone = args.get("phone")
        if isinstance(phone, str) and phone:
            body["phoneNumbers"] = [{"value": phone}]

        provider, err = self._resolve_provider()
        if err is not None:
            return ToolResult(content=err, is_error=True)

        try:
            token = self._take_token(provider, force=False)
            try:
                resp = await self._update_contact(token, resource_name, body)
            except ContactsAPIError as exc:
                if exc.status != 401:
                    raise
                token = self._take_token(provider, force=True)
                resp = await self._update_contact(token, resource_name, body)
        except Exception as exc:
            logger.error(
                "[google_contacts] contacts_update failed: %s",
                self._scrub(str(exc)),
            )
            return ToolResult(
                content=self._scrub(f"Contacts update failed: {exc}"),
                is_error=True,
            )

        if not isinstance(resp, dict):
            return ToolResult(
                content="unexpected Contacts update response", is_error=True
            )
        return ToolResult(content=json.dumps(_shape_contact(resp)))


def create(fetch_fn: FetchFn | None = None) -> GoogleContactsPlugin:
    """Zero-arg-by-default factory the orchestrator discovers.

    ``fetch_fn`` is for tests; production leaves it None. The token
    provider is wired separately via ``set_token_provider`` from
    ``cerebral/main.py``."""
    return GoogleContactsPlugin(fetch_fn=fetch_fn)
