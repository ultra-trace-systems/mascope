"""Socket.IO storage for cross-worker state management.

This module provides Redis-backed storage for socket state that must
be accessible across multiple uvicorn workers.

Components:
- RedisStorageClient: Shared Redis connection
- Room tracking: Cross-worker room membership queries
- Session management: Authentication session CRUD operations
"""

from mascope_backend.socket.storage.client import (
    redis_storage_client,
)
from mascope_backend.socket.storage.exceptions import SocketSessionError
from mascope_backend.socket.storage.rooms import (
    room_tracker,
)
from mascope_backend.socket.storage.services import (
    is_service_connected,
    register_service,
    start_presence_renewal,
    stop_presence_renewal,
    unregister_service,
)
from mascope_backend.socket.storage.sessions import (
    clear_user_session,
    forget_user_socket,
    get_session_user,
    get_user_socket_sids,
    save_user_session,
)


__all__ = [
    "redis_storage_client",
    "room_tracker",
    "save_user_session",
    "clear_user_session",
    "get_session_user",
    "get_user_socket_sids",
    "forget_user_socket",
    "SocketSessionError",
    "register_service",
    "unregister_service",
    "is_service_connected",
    "start_presence_renewal",
    "stop_presence_renewal",
]
