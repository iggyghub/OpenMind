"""
Human-language labels for the 16 capability classes — Issue #48, ADR-0005.

The closed vocabulary in `gate.Capability` is internal: machine-stable, all
lowercase, suitable for logs and storage. The tray's consent prompt and the
Permissions UI (#53) must show the user a short noun phrase ("Write files on
disk") plus a one-sentence explainer ("Allows Felix to ...").

Two parallel `Mapping[Capability, str]` tables live here. Both are
exhaustive — keying every `Capability` is checked at import time, the same
shape as `DEFAULT_POLICY`'s completeness check. Adding a 17th class to the
vocabulary is a deliberate ADR-level change that forces an update here too.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from cerebral.security.gate import Capability


# Short noun phrase. Sentence case, no trailing punctuation. Suitable for a
# notification title or the bold line on a consent prompt.
CAPABILITY_LABEL: Mapping[Capability, str] = MappingProxyType({
    Capability.VAULT_UNLOCK: "Unlock your password vault",
    Capability.SECRETS_READ: "Read a stored secret",
    Capability.FS_READ: "Read files on disk",
    Capability.FS_WRITE: "Write files on disk",
    Capability.FS_DELETE: "Delete files on disk",
    Capability.CLIPBOARD: "Read or write the clipboard",
    Capability.SHELL_EXEC: "Run a shell command",
    Capability.CODE_INSTALL: "Install a plugin or package",
    Capability.NETWORK_EGRESS_LOCAL: "Connect to a local network service",
    Capability.NETWORK_EGRESS_CLOUD: "Connect to a cloud service",
    Capability.NETWORK_RECON: "Scan the local network",
    Capability.NETWORK_CONFIG: "Change network settings",
    Capability.EXTERNAL_DATA_READ: "Read data from an external account",
    Capability.EXTERNAL_DATA_WRITE: "Write data to an external account",
    Capability.DEVICE_CONTROL: "Control a device or system setting",
    Capability.SCREEN_CAPTURE: "Capture the screen",
})


# One-sentence explainer. Plain language, "Felix" not "the assistant". Used
# under the Why? expander on the consent prompt and on the Permissions tab.
CAPABILITY_DESCRIPTION: Mapping[Capability, str] = MappingProxyType({
    Capability.VAULT_UNLOCK: (
        "Felix needs to unlock a password vault to read or use a stored "
        "credential."
    ),
    Capability.SECRETS_READ: (
        "Felix needs to read a stored secret (API key, password, token) "
        "from the system keyring or an environment variable."
    ),
    Capability.FS_READ: (
        "Felix needs to read a file from your computer. Reading is silent "
        "by default; check the path in the preview if you're unsure."
    ),
    Capability.FS_WRITE: (
        "Felix needs to create or modify a file on your computer. Check "
        "the path in the preview before allowing."
    ),
    Capability.FS_DELETE: (
        "Felix needs to delete a file or folder. This is hard to undo — "
        "check the path before allowing."
    ),
    Capability.CLIPBOARD: (
        "Felix needs to read what's currently on your clipboard or write "
        "something into it."
    ),
    Capability.SHELL_EXEC: (
        "Felix needs to run a shell command on your computer. This is the "
        "highest-blast-radius capability — only allow if you trust the "
        "exact command shown."
    ),
    Capability.CODE_INSTALL: (
        "Felix needs to install a plugin or download a Python package. "
        "Plugins extend what Felix can do; check the source and "
        "dependencies."
    ),
    Capability.NETWORK_EGRESS_LOCAL: (
        "Felix needs to talk to a service on your own network (a home "
        "server, a local app like n8n or Ollama)."
    ),
    Capability.NETWORK_EGRESS_CLOUD: (
        "Felix needs to make a network request to a service on the "
        "internet."
    ),
    Capability.NETWORK_RECON: (
        "Felix needs to scan your local network — list devices, ping "
        "hosts, probe ports."
    ),
    Capability.NETWORK_CONFIG: (
        "Felix needs to change a network setting — connect or disconnect "
        "a VPN, switch Wi-Fi, change DNS."
    ),
    Capability.EXTERNAL_DATA_READ: (
        "Felix needs to read data from one of your connected accounts "
        "(Gmail, Google Drive, GitHub, etc.)."
    ),
    Capability.EXTERNAL_DATA_WRITE: (
        "Felix needs to write data to one of your connected accounts — "
        "send a message, post a comment, save a document."
    ),
    Capability.DEVICE_CONTROL: (
        "Felix needs to control a system feature — volume, brightness, "
        "open an app, take a screenshot, talk to a printer or webcam."
    ),
    Capability.SCREEN_CAPTURE: (
        "Felix needs to take a screenshot of your screen. The captured "
        "image stays on your computer unless you ask Felix to share it."
    ),
})


def _check_completeness() -> None:
    """Both tables must key every Capability. Raises at import on a gap."""
    missing_labels = set(Capability) - set(CAPABILITY_LABEL)
    if missing_labels:
        raise RuntimeError(
            "CAPABILITY_LABEL is missing entries for: "
            f"{sorted(c.value for c in missing_labels)}"
        )
    missing_descriptions = set(Capability) - set(CAPABILITY_DESCRIPTION)
    if missing_descriptions:
        raise RuntimeError(
            "CAPABILITY_DESCRIPTION is missing entries for: "
            f"{sorted(c.value for c in missing_descriptions)}"
        )


_check_completeness()


def label_for(capability: Capability) -> str:
    """Return the short noun-phrase label for a capability."""
    return CAPABILITY_LABEL[capability]


def description_for(capability: Capability) -> str:
    """Return the one-sentence explainer for a capability."""
    return CAPABILITY_DESCRIPTION[capability]
