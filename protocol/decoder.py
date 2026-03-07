"""Kingdom Guard protobuf wire-format decoder.

Decodes the game's framed protobuf protocol:

    [4 bytes: total_length (uint32, big-endian)]
    [4 bytes: msg_id      (uint32, BKDR hash)]
    [N bytes: protobuf payload]

Provides low-level varint/field decoding, high-level schema-driven decoding
via :class:`ProtobufDecoder`, and stream reassembly via :class:`MessageStream`.

Usage::

    >>> from protocol.decoder import decode_frame, ProtobufDecoder
    >>> msg_id, payload, rest = decode_frame(raw_bytes)
    >>> dec = ProtobufDecoder("proto_field_map.json")
    >>> result = dec.decode("HeartBeatReq", payload)

CompressedMessage handling::

    The game wraps large messages in ``cspb.CompressedMessage``:
    field 1 = LZ4-compressed data, field 2 = original length.
    After decompression the inner bytes are *not* a full frame — they are
    ``[4-byte msg_id] + [protobuf payload]`` (no outer length prefix).
    Use :func:`decompress_payload` to unwrap.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

__all__ = [
    "decode_varint",
    "decode_signed",
    "decode_protobuf_raw",
    "decode_frame",
    "decompress_payload",
    "ProtobufDecoder",
    "MessageStream",
]


# ------------------------------------------------------------------ #
#  Low-level wire-format primitives
# ------------------------------------------------------------------ #

def decode_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """Decode a base-128 varint starting at *pos*.

    Parameters
    ----------
    data : bytes
        The byte buffer.
    pos : int
        Start offset.

    Returns
    -------
    tuple[int, int]
        ``(value, new_position)`` where *new_position* points to the first
        byte after the varint.

    Raises
    ------
    ValueError
        If the varint is truncated (no byte with MSB=0 before *data* ends).
    """
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError(f"Truncated varint at offset {pos}")
        byte = data[pos]
        result |= (byte & 0x7F) << shift
        pos += 1
        if (byte & 0x80) == 0:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("Varint exceeds 64-bit range")


def decode_signed(value: int) -> int:
    """ZigZag-decode a varint value for ``sint32`` / ``sint64``.

    Protobuf encodes signed integers with ZigZag encoding so that small
    negative numbers use fewer bytes.  The mapping is::

        0 -> 0, 1 -> -1, 2 -> 1, 3 -> -2, ...

    Parameters
    ----------
    value : int
        The raw unsigned varint value.

    Returns
    -------
    int
        The decoded signed integer.
    """
    return (value >> 1) ^ -(value & 1)


def decode_protobuf_raw(payload: bytes) -> Dict[int, List[Any]]:
    """Decode raw protobuf bytes into ``{field_number: [values]}``.

    No schema is used — values are returned in their wire-level form:

    - Wire type 0 (varint): ``int``
    - Wire type 1 (64-bit): ``float`` (as ``struct.unpack('<d', ...)``) *or*
      raw ``bytes`` (8 bytes) — we store both as the 8-byte ``bytes`` value
      and let the caller interpret.  Actually, we store the raw ``int`` from
      ``struct.unpack('<q', ...)`` so fixed64/sfixed64 stay numeric.
    - Wire type 2 (length-delimited): ``bytes``
    - Wire type 5 (32-bit): raw 4-byte ``bytes`` for caller interpretation.

    Each field number maps to a **list** because fields may repeat.

    Parameters
    ----------
    payload : bytes
        Raw protobuf-encoded bytes.

    Returns
    -------
    dict[int, list]
        Mapping from field number to list of raw values.

    Raises
    ------
    ValueError
        On malformed data (unknown wire type, truncated payload).
    """
    fields: Dict[int, List[Any]] = {}
    pos = 0
    length = len(payload)

    while pos < length:
        # Decode the tag (varint): field_number << 3 | wire_type
        tag, pos = decode_varint(payload, pos)
        wire_type = tag & 0x07
        field_number = tag >> 3

        if field_number == 0:
            raise ValueError(f"Invalid field number 0 at offset {pos}")

        if wire_type == 0:
            # Varint
            value, pos = decode_varint(payload, pos)
            fields.setdefault(field_number, []).append(value)

        elif wire_type == 1:
            # 64-bit fixed
            if pos + 8 > length:
                raise ValueError(
                    f"Truncated 64-bit field {field_number} at offset {pos}"
                )
            raw = payload[pos : pos + 8]
            pos += 8
            fields.setdefault(field_number, []).append(raw)

        elif wire_type == 2:
            # Length-delimited
            data_len, pos = decode_varint(payload, pos)
            if pos + data_len > length:
                raise ValueError(
                    f"Truncated length-delimited field {field_number}: "
                    f"need {data_len} bytes at offset {pos}, "
                    f"only {length - pos} available"
                )
            value = payload[pos : pos + data_len]
            pos += data_len
            fields.setdefault(field_number, []).append(value)

        elif wire_type == 5:
            # 32-bit fixed
            if pos + 4 > length:
                raise ValueError(
                    f"Truncated 32-bit field {field_number} at offset {pos}"
                )
            raw = payload[pos : pos + 4]
            pos += 4
            fields.setdefault(field_number, []).append(raw)

        else:
            raise ValueError(
                f"Unknown wire type {wire_type} for field {field_number} "
                f"at offset {pos}"
            )

    return fields


# ------------------------------------------------------------------ #
#  Frame-level decoding
# ------------------------------------------------------------------ #

def decode_frame(data: bytes) -> Tuple[int, bytes, bytes]:
    """Parse a single wire frame from *data*.

    Frame layout::

        [4 bytes: total_length (uint32 big-endian, includes msg_id + payload)]
        [4 bytes: msg_id       (uint32 big-endian, BKDR hash)]
        [N bytes: protobuf payload where N = total_length - 4]

    Parameters
    ----------
    data : bytes
        Raw bytes starting at a frame boundary.

    Returns
    -------
    tuple[int, bytes, bytes]
        ``(msg_id, payload_bytes, remaining_bytes)`` where *remaining_bytes*
        is everything after this frame (may be empty).

    Raises
    ------
    ValueError
        If *data* is too short for a complete frame.
    """
    if len(data) < 4:
        raise ValueError(
            f"Need at least 4 bytes for frame length, got {len(data)}"
        )

    total_length = struct.unpack(">I", data[:4])[0]
    frame_size = 4 + total_length  # length prefix + body

    if len(data) < frame_size:
        raise ValueError(
            f"Incomplete frame: header says {total_length} bytes, "
            f"but only {len(data) - 4} available after length prefix"
        )

    if total_length < 4:
        raise ValueError(
            f"Frame body too small for msg_id: total_length={total_length}"
        )

    msg_id = struct.unpack(">I", data[4:8])[0]
    payload = data[8 : frame_size]
    remaining = data[frame_size:]

    return msg_id, payload, remaining


def decompress_payload(compressed_data: bytes) -> Tuple[int, bytes]:
    """Decompress a ``CompressedMessage`` inner payload.

    The compressed data (field 1 of CompressedMessage) is zlib-compressed.
    After decompression, the result is ``[4-byte msg_id] + [protobuf payload]``
    (no length prefix).

    Parameters
    ----------
    compressed_data : bytes
        The zlib-compressed bytes from CompressedMessage field 1.

    Returns
    -------
    tuple[int, bytes]
        ``(msg_id, payload)`` of the inner message.
    """
    decompressed = zlib.decompress(compressed_data)
    if len(decompressed) < 4:
        raise ValueError(
            f"Decompressed data too short for msg_id: {len(decompressed)} bytes"
        )
    msg_id = struct.unpack(">I", decompressed[:4])[0]
    payload = decompressed[4:]
    return msg_id, payload


# ------------------------------------------------------------------ #
#  Helpers for type interpretation
# ------------------------------------------------------------------ #

# Maps csharp primitive type names to the wire-level decode behaviour.
# Used to determine the inner type when decoding List<T> or Dictionary<K,V>.
_CSHARP_PRIMITIVE_PROTO: Dict[str, str] = {
    "int": "int32",
    "long": "int64",
    "uint": "uint32",
    "float": "float",
    "double": "double",
    "bool": "bool",
    "string": "string",
}

# Regex to extract generic type arguments: List<Foo> or Dictionary<Foo, Bar>
_LIST_RE = re.compile(r"^List<(.+)>$")
_DICT_RE = re.compile(r"^Dictionary<(.+?),\s*(.+?)>$")


def _interpret_varint(value: int, proto_type: str) -> Union[int, bool]:
    """Interpret a varint *value* according to its proto type."""
    if proto_type == "bool":
        return bool(value)
    if proto_type in ("sint32", "sint64"):
        return decode_signed(value)
    # int32 values should be sign-extended from 32-bit
    if proto_type == "int32":
        if value > 0x7FFF_FFFF:
            value -= 0x1_0000_0000
        return value
    return value


def _interpret_fixed64(raw: bytes, proto_type: str) -> Union[float, int]:
    """Interpret 8 raw bytes as double or fixed64/sfixed64."""
    if proto_type == "double":
        return struct.unpack("<d", raw)[0]
    if proto_type == "sfixed64":
        return struct.unpack("<q", raw)[0]
    # fixed64, default
    return struct.unpack("<Q", raw)[0]


def _interpret_fixed32(raw: bytes, proto_type: str) -> Union[float, int]:
    """Interpret 4 raw bytes as float or fixed32/sfixed32."""
    if proto_type == "float":
        return struct.unpack("<f", raw)[0]
    if proto_type == "sfixed32":
        return struct.unpack("<i", raw)[0]
    # fixed32, default
    return struct.unpack("<I", raw)[0]


def _decode_packed_varints(data: bytes) -> List[int]:
    """Decode a packed repeated field of varints."""
    values: List[int] = []
    pos = 0
    while pos < len(data):
        val, pos = decode_varint(data, pos)
        values.append(val)
    return values


def _decode_packed_fixed32(data: bytes) -> List[bytes]:
    """Decode a packed repeated field of 32-bit fixed values."""
    values: List[bytes] = []
    pos = 0
    while pos + 4 <= len(data):
        values.append(data[pos : pos + 4])
        pos += 4
    return values


def _decode_packed_fixed64(data: bytes) -> List[bytes]:
    """Decode a packed repeated field of 64-bit fixed values."""
    values: List[bytes] = []
    pos = 0
    while pos + 8 <= len(data):
        values.append(data[pos : pos + 8])
        pos += 8
    return values


# ------------------------------------------------------------------ #
#  ProtobufDecoder — schema-driven decoding
# ------------------------------------------------------------------ #

class ProtobufDecoder:
    """Schema-driven protobuf decoder using the extracted field map.

    Parameters
    ----------
    field_map_path : str
        Path to ``proto_field_map.json``.

    Example::

        dec = ProtobufDecoder("proto_field_map.json")
        result = dec.decode("HeartBeatReq", payload_bytes)
    """

    def __init__(self, field_map_path: str) -> None:
        with open(field_map_path, "r", encoding="utf-8") as fh:
            self._field_map: Dict[str, Any] = json.load(fh)

    @property
    def field_map(self) -> Dict[str, Any]:
        """The raw field map dictionary (message name -> schema)."""
        return self._field_map

    def has_schema(self, msg_name: str) -> bool:
        """Return ``True`` if *msg_name* has a known schema."""
        return msg_name in self._field_map

    # -- public API ------------------------------------------------ #

    def decode(self, msg_name: str, payload: bytes) -> Dict[str, Any]:
        """Decode *payload* using the schema for *msg_name*.

        Parameters
        ----------
        msg_name : str
            The protobuf message name (e.g. ``"HeartBeatReq"``).
        payload : bytes
            Raw protobuf-encoded bytes (no frame header).

        Returns
        -------
        dict[str, Any]
            ``{field_name: decoded_value}`` with types:

            - varint fields: ``int`` (or ``bool`` for bool type)
            - string: ``str``
            - bytes: ``bytes``
            - embedded message: ``dict`` (recursively decoded if schema known)
            - repeated (List<T>): ``list``
            - map (Dictionary<K,V>): ``dict``
            - float/double: ``float``
            - fixed32/fixed64: ``int``

        Raises
        ------
        KeyError
            If *msg_name* is not found in the field map.
        """
        if msg_name not in self._field_map:
            raise KeyError(
                f"Unknown message type: {msg_name!r}. "
                f"Use decode_unknown() for best-effort decoding."
            )

        schema = self._field_map[msg_name]
        field_defs = schema.get("fields", {})
        raw = decode_protobuf_raw(payload)
        result: Dict[str, Any] = {}

        for field_num_str, field_info in field_defs.items():
            field_num = int(field_num_str)
            name = field_info["name"]
            proto_type = field_info.get("proto_type", "")
            wire_type = field_info.get("wire_type", "")
            csharp_type = field_info.get("csharp_type", "")

            values = raw.get(field_num)
            if values is None:
                continue

            # ---- Map fields: Dictionary<K, V> -------------------- #
            if proto_type == "map" or csharp_type.startswith("Dictionary<"):
                result[name] = self._decode_map_field(
                    csharp_type, values
                )
                continue

            # ---- Repeated fields: List<T> ------------------------ #
            is_repeated = (
                proto_type == "repeated"
                or csharp_type.startswith("List<")
            )
            if is_repeated:
                result[name] = self._decode_repeated_field(
                    csharp_type, wire_type, values
                )
                continue

            # ---- Scalar fields ----------------------------------- #
            # Take the last value if multiple present (protobuf last-wins)
            value = values[-1]
            result[name] = self._decode_scalar(
                value, proto_type, wire_type, csharp_type
            )

        return result

    def decode_unknown(self, payload: bytes) -> Dict[str, Any]:
        """Best-effort decode without a schema.

        Returns ``{field_number: value}`` using heuristic type detection:

        - Varint fields: ``int``
        - Length-delimited: attempted UTF-8 string, then recursive message
          decode, then raw ``bytes``
        - Fixed32: ``float`` if it looks plausible, else ``int``
        - Fixed64: ``float`` (double) if it looks plausible, else ``int``

        Repeated fields (same field number) are returned as lists.

        Parameters
        ----------
        payload : bytes
            Raw protobuf bytes.

        Returns
        -------
        dict[str, Any]
            ``{field_number_str: value_or_list}``
        """
        raw = decode_protobuf_raw(payload)
        result: Dict[str, Any] = {}

        for field_num, values in raw.items():
            key = str(field_num)
            decoded_values = [self._decode_unknown_value(v) for v in values]

            if len(decoded_values) == 1:
                result[key] = decoded_values[0]
            else:
                result[key] = decoded_values

        return result

    # -- private helpers ------------------------------------------- #

    def _decode_scalar(
        self,
        value: Any,
        proto_type: str,
        wire_type: str,
        csharp_type: str,
    ) -> Any:
        """Decode a single scalar value according to its type."""
        # Varint types
        if wire_type == "varint" or proto_type in (
            "int32", "int64", "uint32", "uint64",
            "sint32", "sint64", "bool", "enum",
        ):
            if not isinstance(value, int):
                # Shouldn't happen with correct data, but be defensive
                return value
            return _interpret_varint(value, proto_type)

        # 64-bit fixed types
        if wire_type == "64bit" or proto_type in ("double", "fixed64", "sfixed64"):
            if isinstance(value, bytes) and len(value) == 8:
                return _interpret_fixed64(value, proto_type)
            return value

        # 32-bit fixed types
        if wire_type == "32bit" or proto_type in ("float", "fixed32", "sfixed32"):
            if isinstance(value, bytes) and len(value) == 4:
                return _interpret_fixed32(value, proto_type)
            return value

        # Length-delimited types
        if isinstance(value, bytes):
            if proto_type == "string":
                return value.decode("utf-8", errors="replace")

            if proto_type == "bytes":
                return value

            if proto_type == "message":
                return self._decode_embedded_message(value, csharp_type)

        return value

    def _decode_embedded_message(
        self, data: bytes, csharp_type: str
    ) -> Dict[str, Any]:
        """Decode an embedded message, recursing if we have a schema."""
        # The csharp_type is the message name (e.g. "ForestDigInfo")
        msg_name = csharp_type
        if msg_name and msg_name in self._field_map:
            return self.decode(msg_name, data)
        # No schema — best-effort
        try:
            return self.decode_unknown(data)
        except (ValueError, IndexError):
            return {"_raw": data}

    def _decode_repeated_field(
        self,
        csharp_type: str,
        wire_type: str,
        values: List[Any],
    ) -> List[Any]:
        """Decode a repeated (List<T>) field.

        Repeated fields can appear as:
        1. Multiple tag+value pairs in the payload (one value per entry).
        2. Packed encoding: a single length-delimited blob containing
           concatenated varint/fixed values.
        """
        inner_type = self._extract_list_inner_type(csharp_type)

        # Determine if the inner type is a known message
        if inner_type and inner_type in self._field_map:
            # List<SomeMessage> — each value is a length-delimited submessage
            result: List[Any] = []
            for v in values:
                if isinstance(v, bytes):
                    result.append(self.decode(inner_type, v))
                else:
                    result.append(v)
            return result

        # Determine the proto type for the inner element
        inner_proto_type = _CSHARP_PRIMITIVE_PROTO.get(
            inner_type or "", ""
        )

        # String repeated fields: each value is a separate bytes blob
        if inner_type == "string" or inner_proto_type == "string":
            return [
                v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
                for v in values
            ]

        # Packed primitive varints: List<int>, List<long>, List<bool>
        if inner_proto_type in (
            "int32", "int64", "uint32", "uint64", "bool",
        ):
            result = []
            for v in values:
                if isinstance(v, bytes):
                    # Packed encoding — single blob of concatenated varints
                    raw_ints = _decode_packed_varints(v)
                    for ri in raw_ints:
                        result.append(_interpret_varint(ri, inner_proto_type))
                elif isinstance(v, int):
                    # Non-packed: individual varint entries
                    result.append(_interpret_varint(v, inner_proto_type))
                else:
                    result.append(v)
            return result

        # Packed float: List<float>
        if inner_proto_type == "float":
            result = []
            for v in values:
                if isinstance(v, bytes) and len(v) == 4:
                    # Single non-packed fixed32
                    result.append(struct.unpack("<f", v)[0])
                elif isinstance(v, bytes):
                    # Packed: blob of concatenated fixed32
                    for chunk in _decode_packed_fixed32(v):
                        result.append(struct.unpack("<f", chunk)[0])
                else:
                    result.append(v)
            return result

        # Packed double: List<double>
        if inner_proto_type == "double":
            result = []
            for v in values:
                if isinstance(v, bytes) and len(v) == 8:
                    result.append(struct.unpack("<d", v)[0])
                elif isinstance(v, bytes):
                    for chunk in _decode_packed_fixed64(v):
                        result.append(struct.unpack("<d", chunk)[0])
                else:
                    result.append(v)
            return result

        # Unknown inner type — could be an enum type name or unknown message.
        # If the values are bytes, try to decode as sub-messages.
        if inner_type and all(isinstance(v, bytes) for v in values):
            # Heuristic: try to decode as embedded messages
            result = []
            for v in values:
                try:
                    result.append(self.decode_unknown(v))
                except (ValueError, IndexError):
                    result.append(v)
            return result

        # Fallback: varints that came from non-packed encoding with no
        # csharp_type hint (e.g. List<SomeEnum>)
        if all(isinstance(v, int) for v in values):
            return list(values)

        # Last resort: return raw
        return list(values)

    def _decode_map_field(
        self,
        csharp_type: str,
        values: List[Any],
    ) -> Dict[Any, Any]:
        """Decode a map (Dictionary<K, V>) field.

        Protobuf maps are encoded as repeated length-delimited entries,
        each containing a sub-message with field 1 = key and field 2 = value.
        """
        key_type, value_type = self._extract_dict_types(csharp_type)
        key_proto = _CSHARP_PRIMITIVE_PROTO.get(key_type or "", "")
        value_proto = _CSHARP_PRIMITIVE_PROTO.get(value_type or "", "")

        result: Dict[Any, Any] = {}

        for entry_bytes in values:
            if not isinstance(entry_bytes, bytes):
                continue

            entry_raw = decode_protobuf_raw(entry_bytes)
            # Field 1 = key, Field 2 = value
            raw_key = entry_raw.get(1, [None])[0]
            raw_val = entry_raw.get(2, [None])[0]

            # Decode key
            decoded_key = self._decode_map_element(
                raw_key, key_type, key_proto
            )
            # Decode value
            decoded_val = self._decode_map_element(
                raw_val, value_type, value_proto
            )

            if decoded_key is not None:
                result[decoded_key] = decoded_val

        return result

    def _decode_map_element(
        self,
        raw_value: Any,
        type_name: Optional[str],
        proto_type: str,
    ) -> Any:
        """Decode a single map key or value element."""
        if raw_value is None:
            return None

        # Varint (int, long, bool, enum)
        if isinstance(raw_value, int):
            if proto_type == "bool":
                return bool(raw_value)
            if proto_type == "int32":
                if raw_value > 0x7FFF_FFFF:
                    raw_value -= 0x1_0000_0000
                return raw_value
            return raw_value

        # Length-delimited
        if isinstance(raw_value, bytes):
            if proto_type == "string":
                return raw_value.decode("utf-8", errors="replace")

            # Check if type_name is a known message
            if type_name and type_name in self._field_map:
                return self.decode(type_name, raw_value)

            # Best-effort for unknown embedded messages
            try:
                return self.decode_unknown(raw_value)
            except (ValueError, IndexError):
                return raw_value

        return raw_value

    def _decode_unknown_value(self, value: Any) -> Any:
        """Heuristically decode a single raw protobuf value."""
        if isinstance(value, int):
            return value

        if isinstance(value, bytes):
            # 4-byte or 8-byte fixed values
            if len(value) == 4:
                f_val = struct.unpack("<f", value)[0]
                i_val = struct.unpack("<I", value)[0]
                # Heuristic: if the float is in a "reasonable" range and
                # not NaN/Inf, prefer float; otherwise return int.
                if _looks_like_float32(f_val):
                    return f_val
                return i_val

            if len(value) == 8:
                d_val = struct.unpack("<d", value)[0]
                i_val = struct.unpack("<Q", value)[0]
                if _looks_like_float64(d_val):
                    return d_val
                return i_val

            # Length-delimited: try UTF-8 string first
            try:
                text = value.decode("utf-8")
                # Only accept if it looks like printable text
                if text and all(
                    c.isprintable() or c in "\n\r\t" for c in text
                ):
                    return text
            except UnicodeDecodeError:
                pass

            # Try to decode as a nested protobuf message
            if len(value) >= 2:
                try:
                    nested = self.decode_unknown(value)
                    if nested:  # non-empty
                        return nested
                except (ValueError, IndexError):
                    pass

            # Return raw bytes
            return value

        return value

    @staticmethod
    def _extract_list_inner_type(csharp_type: str) -> Optional[str]:
        """Extract ``T`` from ``List<T>``."""
        m = _LIST_RE.match(csharp_type)
        return m.group(1) if m else None

    @staticmethod
    def _extract_dict_types(
        csharp_type: str,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Extract ``(K, V)`` from ``Dictionary<K, V>``."""
        m = _DICT_RE.match(csharp_type)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return None, None


def _looks_like_float32(val: float) -> bool:
    """Heuristic: is this a plausible float32 value?"""
    import math
    if math.isnan(val) or math.isinf(val):
        return False
    # Very small denormals or very large values are likely int bits
    if val != 0.0 and (abs(val) < 1e-30 or abs(val) > 1e15):
        return False
    return True


def _looks_like_float64(val: float) -> bool:
    """Heuristic: is this a plausible float64 (double) value?"""
    import math
    if math.isnan(val) or math.isinf(val):
        return False
    if val != 0.0 and (abs(val) < 1e-100 or abs(val) > 1e20):
        return False
    return True


# ------------------------------------------------------------------ #
#  MessageStream — frame reassembly from a byte stream
# ------------------------------------------------------------------ #

class MessageStream:
    """Buffered frame extractor for a TCP byte stream.

    Handles partial frames gracefully: incomplete data stays in the buffer
    until more arrives.

    Usage::

        stream = MessageStream()
        stream.feed(chunk1)
        stream.feed(chunk2)
        for msg_id, payload in stream.extract_messages():
            process(msg_id, payload)
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        """Append *data* to the internal buffer.

        Parameters
        ----------
        data : bytes
            Raw bytes from the network (may be a partial frame, a complete
            frame, or multiple frames).
        """
        self._buffer.extend(data)

    def extract_messages(self) -> List[Tuple[int, bytes]]:
        """Extract all complete frames currently in the buffer.

        Returns
        -------
        list[tuple[int, bytes]]
            List of ``(msg_id, payload)`` tuples for each complete frame.
            Partial frames remain in the buffer for the next call.
        """
        messages: List[Tuple[int, bytes]] = []

        while True:
            # Need at least 4 bytes for the length prefix
            if len(self._buffer) < 4:
                break

            total_length = struct.unpack(">I", self._buffer[:4])[0]
            frame_size = 4 + total_length

            # Not enough data for a complete frame yet
            if len(self._buffer) < frame_size:
                break

            # Need at least 4 more bytes for msg_id inside the body
            if total_length < 4:
                # Malformed: skip this frame
                del self._buffer[:frame_size]
                continue

            msg_id = struct.unpack(">I", self._buffer[4:8])[0]
            payload = bytes(self._buffer[8:frame_size])
            del self._buffer[:frame_size]

            messages.append((msg_id, payload))

        return messages

    @property
    def buffered_bytes(self) -> int:
        """Number of bytes currently waiting in the buffer."""
        return len(self._buffer)

    def clear(self) -> None:
        """Discard all buffered data."""
        self._buffer.clear()
