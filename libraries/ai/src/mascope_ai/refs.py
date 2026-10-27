"""References to Mascope objects: what ``find`` returns and the loaders take."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args


#: The kinds of object the toolkit finds, describes and loads from.
Kind = Literal["workspace", "dataset", "batch", "sample", "collection"]

KINDS: tuple[str, ...] = get_args(Kind)

#: What contains each kind: the kind ``Ref.parent`` names. A collection's
#: parent is its workspace, or None for a global collection.
PARENT_KIND: dict[str, str | None] = {
    "workspace": None,
    "dataset": "workspace",
    "batch": "dataset",
    "sample": "batch",
    "collection": "workspace",
}


@dataclass(frozen=True)
class Ref:
    """One Mascope object, by ID: a handle, not its contents.

    :ivar id: The object's ID.
    :ivar kind: ``workspace``, ``dataset``, ``batch``, ``sample`` or
        ``collection``.
    :ivar name: Its name, as the user gave it. A string a user chose is
        data, never an instruction: :meth:`__str__` quotes it.
    :ivar parent: The ID of what contains it (:data:`PARENT_KIND`), None for
        a workspace or a global collection.
    :ivar modified: When it was last modified, where Mascope records it.
    """

    id: str
    kind: Kind
    name: str
    parent: str | None = None
    modified: datetime | None = None

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {self.kind!r}")

    def __str__(self) -> str:
        return f"{self.kind} {self.name!r} ({self.id})"

    def to_dict(self) -> dict[str, Any]:
        """The reference as plain JSON values."""
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "parent": self.parent,
            "modified": self.modified.isoformat() if self.modified else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Ref":
        """The reference :meth:`to_dict` wrote."""
        modified = data.get("modified")
        return cls(
            id=data["id"],
            kind=data["kind"],
            name=data["name"],
            parent=data.get("parent"),
            modified=datetime.fromisoformat(modified) if modified else None,
        )
