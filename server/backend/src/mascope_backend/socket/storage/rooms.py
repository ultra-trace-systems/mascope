"""Redis-based room membership tracking for multi-worker Socket.IO.

Tracks which users are subscribed to which rooms.
Enables cross-worker queries like "is user X in room Y?".

Architecture:
- Bidirectional tracking for O(1) lookups both ways
- TTL on all keys prevents memory leaks from orphaned data
- Primary cleanup on disconnect, TTL as safety net

Redis keys:
    mascope:rooms:members:{room_id} - SET of user_ids in room
    mascope:rooms:user:{user_id} - SET of room_ids user is in
    mascope:sockets:rooms:{sid} - SET of room_ids one web socket is in

The per-socket set exists because the Socket.IO server's own record of a
socket's rooms lives only in the worker holding the connection: to make another
worker's socket leave its rooms, something has to say which rooms those are.
"""

import os

from mascope_backend.runtime import runtime
from mascope_backend.socket.storage.client import redis_storage_client
from mascope_backend.socket.storage.config import storage_config


class RoomTracker:
    """
    Track user room membership using Redis Sets.

    Provides cross-worker room membership queries for notification
    routing.
    """

    @staticmethod
    async def join(user_id: int, room_id: str) -> None:
        """
        User joins a room.

        Records membership in both directions for bidirectional queries.
        Automatically sets TTL to prevent memory leaks from orphaned data.

        :param user_id: User ID (converted to string for Redis)
        :type user_id: int
        :param room_id: Room identifier (e.g., "batch-123")
        :type room_id: str
        :raises Exception: If Redis operation fails
        """
        worker_pid = os.getpid()
        try:
            client = redis_storage_client.client

            # Bidirectional tracking for O(1) lookups
            await client.sadd(storage_config.members_key(room_id), str(user_id))
            await client.expire(
                storage_config.members_key(room_id), storage_config.room_ttl
            )

            await client.sadd(storage_config.user_key(user_id), room_id)
            await client.expire(
                storage_config.user_key(user_id), storage_config.room_ttl
            )

            runtime.logger.debug(
                f"Room tracker: user {user_id} joined room '{room_id}' [Worker {worker_pid}]"
            )

        except Exception as e:
            runtime.logger.exception(
                f"Room tracker: failed to join room '{room_id}' for user {user_id}: {e} "
                f"[Worker {worker_pid}]"
            )
            raise

    @staticmethod
    async def leave(user_id: int, room_id: str) -> None:
        """
        User leaves a room.

        Removes membership from both tracking directions. Safe to call
        multiple times (idempotent).

        :param user_id: User ID
        :type user_id: int
        :param room_id: Room identifier
        :type room_id: str
        """
        worker_pid = os.getpid()
        try:
            client = redis_storage_client.client

            await client.srem(storage_config.members_key(room_id), str(user_id))
            await client.srem(storage_config.user_key(user_id), room_id)

            runtime.logger.debug(
                f"Room tracker: user {user_id} left room '{room_id}' [Worker {worker_pid}]"
            )

        except Exception as e:
            runtime.logger.exception(
                f"Room tracker: failed to leave room '{room_id}' for user {user_id}: {e} "
                f"[Worker {worker_pid}]"
            )

    @staticmethod
    async def is_in_room(user_id: int, room_id: str) -> bool:
        """
        Check if user is in room (O(1) operation).

        Used by notification service to determine routing:
        - If True: emit to room (user + observers see it)
        - If False: emit to user's personal room only

        :param user_id: User ID
        :type user_id: int
        :param room_id: Room identifier
        :type room_id: str
        :return: True if user is member of room, False otherwise
        :rtype: bool
        """
        try:
            client = redis_storage_client.client

            return await client.sismember(
                storage_config.members_key(room_id), str(user_id)
            )

        except Exception as e:
            runtime.logger.exception(
                f"Room tracker: failed to check membership for user {user_id} "
                f"in room '{room_id}': {e}"
            )
            # Fail open - assume not in room to use user room fallback
            return False

    @staticmethod
    async def leave_all(user_id: int) -> None:
        """
        Remove user from all rooms (called on disconnect).

        Performs bulk cleanup by first querying all rooms user is in,
        then removing membership from each room's member set.

        :param user_id: User ID
        :type user_id: int
        """
        worker_pid = os.getpid()
        try:
            client = redis_storage_client.client

            # Get all rooms user is in
            rooms = await client.smembers(storage_config.user_key(user_id))

            if not rooms:
                runtime.logger.trace(
                    f"Room tracker: no rooms to clean up for user {user_id} "
                    f"[Worker {worker_pid}]"
                )
                return

            # Remove user from each room's member set
            for room_id in rooms:
                await client.srem(storage_config.members_key(room_id), str(user_id))

            # Delete user's room list
            await client.delete(storage_config.user_key(user_id))

            runtime.logger.debug(
                f"Room tracker: cleaned up {len(rooms)} rooms for user {user_id} "
                f"[Worker {worker_pid}]"
            )

        except Exception as e:
            runtime.logger.exception(
                f"Room tracker: failed to clean up rooms for user {user_id}: {e} "
                f"[Worker {worker_pid}]"
            )

    @staticmethod
    async def _held_elsewhere(user_id: int, sid: str) -> set[str] | None:
        """
        The rooms the user's other web sockets hold, or ``None`` if none is left.

        Only sockets with a live session count: one gone without a disconnect
        (a worker died) holds nothing.

        :param user_id: User ID
        :type user_id: int
        :param sid: The socket to leave out
        :type sid: str
        :return: Room identifiers, or ``None`` when the user has no other socket
        :rtype: set[str] | None
        """
        client = redis_storage_client.client
        others = set(
            await client.smembers(storage_config.user_sockets_key(user_id))
        ) - {sid}
        held: set[str] = set()
        live = False
        for other in others:
            if not await client.exists(storage_config.session_key(other, "/")):
                continue
            live = True
            held |= await RoomTracker.socket_rooms(other)
        return held if live else None

    @staticmethod
    async def release(user_id: int, sid: str, room_id: str) -> None:
        """
        Drop the user's membership of a room one web socket has left.

        A room another of the user's sockets still holds stays the user's, so
        closing a room in one tab does not tell notification routing that the
        user has stopped listening there while a second tab is in it.

        :param user_id: User ID
        :type user_id: int
        :param sid: The socket that left the room
        :type sid: str
        :param room_id: Room identifier
        :type room_id: str
        """
        try:
            held = await RoomTracker._held_elsewhere(user_id, sid)
        except Exception as e:
            # Leave anyway: a membership left behind would route the user's
            # notifications to a room nobody of theirs may be listening in.
            runtime.logger.exception(
                f"Room tracker: failed to read the other sockets of user {user_id}: {e}"
            )
            held = None
        if held is None or room_id not in held:
            await RoomTracker.leave(user_id, room_id)

    @staticmethod
    async def release_socket(user_id: int, sid: str) -> None:
        """
        Drop the user's membership of the rooms of a disconnecting web socket.

        As :meth:`release`, for every room the socket's record names. When no
        other socket of the user is left, every room of the user's goes, as
        :meth:`leave_all` does, including any no socket record names.

        :param user_id: User ID
        :type user_id: int
        :param sid: The socket that disconnected
        :type sid: str
        """
        try:
            # Out of the user's socket index before the others are read: of
            # two tabs closing at once, the one that reads second then finds
            # the first gone and clears every room, where each would otherwise
            # see the other live and leave none.
            await redis_storage_client.client.srem(
                storage_config.user_sockets_key(user_id), sid
            )
            held = await RoomTracker._held_elsewhere(user_id, sid)
            rooms = await RoomTracker.socket_rooms(sid)
        except Exception as e:
            # Clean up as before the per-socket record: a membership left
            # behind would route the user's notifications to a room nobody of
            # theirs may be listening in.
            runtime.logger.exception(
                f"Room tracker: failed to read the sockets of user {user_id}: {e}"
            )
            held = None
        if held is None:
            await RoomTracker.leave_all(user_id)
            return
        for room_id in rooms - held:
            await RoomTracker.leave(user_id, room_id)

    @staticmethod
    async def join_socket(sid: str, room_id: str) -> None:
        """
        Record that one web socket joined a room.

        :param sid: Socket.IO session ID
        :type sid: str
        :param room_id: Room identifier
        :type room_id: str
        """
        client = redis_storage_client.client
        await client.sadd(storage_config.socket_rooms_key(sid), room_id)
        await client.expire(
            storage_config.socket_rooms_key(sid), storage_config.room_ttl
        )

    @staticmethod
    async def leave_socket(sid: str, room_id: str) -> None:
        """
        Record that one web socket left a room.

        :param sid: Socket.IO session ID
        :type sid: str
        :param room_id: Room identifier
        :type room_id: str
        """
        await redis_storage_client.client.srem(
            storage_config.socket_rooms_key(sid), room_id
        )

    @staticmethod
    async def socket_rooms(sid: str) -> set[str]:
        """
        The rooms one web socket has joined, whichever worker holds it.

        :param sid: Socket.IO session ID
        :type sid: str
        :return: Room identifiers
        :rtype: set[str]
        """
        return set(
            await redis_storage_client.client.smembers(
                storage_config.socket_rooms_key(sid)
            )
        )

    @staticmethod
    async def forget_socket(sid: str) -> None:
        """
        Drop one web socket's room record (called on disconnect).

        :param sid: Socket.IO session ID
        :type sid: str
        """
        try:
            await redis_storage_client.client.delete(
                storage_config.socket_rooms_key(sid)
            )
        except Exception as e:
            runtime.logger.exception(
                f"Room tracker: failed to forget the rooms of socket {sid}: {e}"
            )


room_tracker = RoomTracker()
