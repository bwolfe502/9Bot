"""Kingdom Guard protocol registry — BKDR hash and message ID mapping.

The game uses a BKDR hash (seed=131, confirmed from ARM64 disassembly) to
derive a 32-bit message ID from protobuf class names.

**Two hashing modes exist:**

- **Internal (code-level):** ``BKDR("cspb.HeartBeatReq")`` — used in
  PType.HashCode for reflection-based dispatch inside the game binary.
- **Wire protocol:** ``BKDR("HeartBeatReq")`` — bare class name without
  namespace prefix. This is what appears on the wire as the 4-byte msg_id.

Telemetry messages use the prefix ``TFW.`` in both modes.

Usage::

    >>> from protocol.registry import bkdr_hash, wire_id, get_wire_registry
    >>> bkdr_hash("HeartBeatReq")
    1241063862
    >>> wire_id("HeartBeatReq")
    1241063862
    >>> r = get_wire_registry()
    >>> r.name(1241063862)
    'HeartBeatReq'
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

__all__ = [
    "bkdr_hash",
    "msg_id",
    "wire_id",
    "Registry",
    "get_registry",
    "get_wire_registry",
]

_UINT32_MASK = 0xFFFF_FFFF

# 12 known TFW telemetry message types
TFW_NAMES: list[str] = [
    "UserClick",
    "UserEvent",
    "UserLogin",
    "UserLogout",
    "UserOnline",
    "UserPay",
    "UserRegister",
    "UserSetOnce",
    "UserSet",
    "UserUnset",
    "UserAppend",
    "UserDel",
]


# ------------------------------------------------------------------ #
#  BKDR hash
# ------------------------------------------------------------------ #

def bkdr_hash(s: str, seed: int = 131) -> int:
    """Compute a BKDR hash with uint32 overflow semantics.

    Parameters
    ----------
    s : str
        The input string (e.g. ``"cspb.HeartBeatReq"``).
    seed : int
        Hash multiplier.  The game binary uses 131.

    Returns
    -------
    int
        32-bit unsigned hash value.
    """
    h = 0
    for ch in s:
        h = ((h * seed) + ord(ch)) & _UINT32_MASK
    return h


def msg_id(class_name: str) -> int:
    """Return the *internal* message ID for a ``cspb.*`` class name.

    This is the hash used inside the game binary for PType.HashCode.
    **Not** the wire-level msg_id — use :func:`wire_id` for that.

    >>> msg_id("HeartBeatReq")
    205673070
    """
    return bkdr_hash(f"cspb.{class_name}")


def wire_id(class_name: str) -> int:
    """Return the wire-protocol message ID (bare class name hash).

    This is the 4-byte msg_id that appears on the wire in
    ``NetMsgData.FromByte`` / ``NetMsgData.MakeByte``.

    >>> wire_id("HeartBeatReq")
    1241063862
    >>> wire_id("EntitiesNtf")
    414712867
    """
    return bkdr_hash(class_name)


def tfw_id(class_name: str) -> int:
    """Return the message ID for a ``TFW.*`` telemetry class name.

    >>> tfw_id("UserClick")  # doctest: +SKIP
    ...
    """
    return bkdr_hash(f"TFW.{class_name}")


# ------------------------------------------------------------------ #
#  Registry
# ------------------------------------------------------------------ #

class Registry:
    """Two-way mapping between message IDs and class names.

    Supports both ``cspb.*`` game messages and ``TFW.*`` telemetry messages.
    The stored *name* is the short class name (without prefix).  The prefix
    is implicit: names from :file:`cspb_message_names.txt` are ``cspb.*``
    and the 12 TFW names are ``TFW.*``.
    """

    def __init__(self) -> None:
        self._id_to_name: Dict[int, str] = {}
        self._name_to_id: Dict[str, int] = {}

    # -- mutators -------------------------------------------------- #

    def register(self, class_name: str, *, prefix: str = "cspb") -> int:
        """Register *class_name* and return its message ID.

        Parameters
        ----------
        class_name : str
            Short class name (e.g. ``"HeartBeatReq"``).
        prefix : str
            Namespace prefix used for hashing (``"cspb"``, ``"TFW"``, or
            ``""`` for bare wire-protocol hashes).

        Returns
        -------
        int
            The 32-bit message ID.
        """
        hash_input = f"{prefix}.{class_name}" if prefix else class_name
        mid = bkdr_hash(hash_input)
        self._id_to_name[mid] = class_name
        self._name_to_id[class_name] = mid
        return mid

    # -- queries --------------------------------------------------- #

    def name(self, mid: int) -> Optional[str]:
        """Reverse lookup: message ID -> class name (or ``None``)."""
        return self._id_to_name.get(mid)

    def id(self, class_name: str) -> Optional[int]:
        """Forward lookup: class name -> message ID (or ``None``)."""
        return self._name_to_id.get(class_name)

    def __len__(self) -> int:
        return len(self._id_to_name)

    def __contains__(self, item: object) -> bool:
        """Membership test — works with both ``int`` (msg_id) and ``str`` (name)."""
        if isinstance(item, int):
            return item in self._id_to_name
        if isinstance(item, str):
            return item in self._name_to_id
        return False

    def __repr__(self) -> str:
        return f"Registry({len(self)} messages)"

    # -- serialisation --------------------------------------------- #

    @classmethod
    def from_names_file(cls, path: str | Path, *, include_tfw: bool = True) -> Registry:
        """Build an *internal* (cspb-prefixed) registry from a names file.

        Parameters
        ----------
        path : str | Path
            Path to a text file (e.g. ``cspb_message_names.txt``).
        include_tfw : bool
            If ``True`` (default), also register the 12 TFW telemetry names.
        """
        reg = cls()
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                name = line.strip()
                if name:
                    reg.register(name, prefix="cspb")
        if include_tfw:
            for tfw_name in TFW_NAMES:
                reg.register(tfw_name, prefix="TFW")
        return reg

    @classmethod
    def from_names_file_wire(cls, path: str | Path) -> Registry:
        """Build a *wire-protocol* registry (bare class name hashes).

        Parameters
        ----------
        path : str | Path
            Path to a text file with one class name per line.
        """
        reg = cls()
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                name = line.strip()
                if name:
                    reg.register(name, prefix="")
        return reg

    @classmethod
    def from_json(cls, path: str | Path) -> Registry:
        """Load a pre-computed registry from JSON.

        The JSON format is ``{msg_id_str: class_name, ...}``.
        """
        reg = cls()
        with open(path, "r", encoding="utf-8") as fh:
            data: dict = json.load(fh)
        for mid_str, name in data.items():
            mid = int(mid_str)
            reg._id_to_name[mid] = name
            reg._name_to_id[name] = mid
        return reg

    def save_json(self, path: str | Path) -> None:
        """Save ``{msg_id_str: name}`` sorted by ID."""
        data = {str(k): v for k, v in sorted(self._id_to_name.items())}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")


# ------------------------------------------------------------------ #
#  Module-level default registry (lazy-loaded)
# ------------------------------------------------------------------ #

_default_registry: Optional[Registry] = None
_wire_registry: Optional[Registry] = None


def get_registry() -> Registry:
    """Return the *internal* (cspb-prefixed) registry from ``registry.json``.

    For wire-level msg_id resolution, use :func:`get_wire_registry` instead.
    """
    global _default_registry
    if _default_registry is None:
        json_path = Path(__file__).with_name("registry.json")
        _default_registry = Registry.from_json(json_path)
    return _default_registry


def get_wire_registry() -> Registry:
    """Return the wire-protocol registry from ``wire_registry.json``.

    This registry maps bare-class-name BKDR hashes to class names,
    matching the msg_ids that appear on the wire in NetMsgData frames.
    """
    global _wire_registry
    if _wire_registry is None:
        json_path = Path(__file__).with_name("wire_registry.json")
        _wire_registry = Registry.from_json(json_path)
    return _wire_registry
