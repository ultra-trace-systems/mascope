"""Socket storage configuration.

Centralized configuration for Redis-backed socket state storage including
session management and room tracking.
"""

from pydantic import BaseModel


class SocketStorageConfig(BaseModel):
    """
    Configuration for socket storage (sessions, rooms, etc.).

    Defines TTL values and Redis key prefixes for socket-related state.
    """

    # TTL settings (in seconds)
    session_ttl: int = 86400  # 24 hours - Socket session lifetime
    room_ttl: int = 86400  # 24 hours - Room membership tracking lifetime

    # Service presence keys are short-lived and refreshed while the service
    # stays connected, so a key can never outlive the process that wrote it: an
    # unclean backend death (power loss, OOM, `docker kill`) runs no disconnect
    # handler, and without expiry the stale key would report a converter
    # connected until Redis was flushed. The TTL bounds that window; the renewal
    # interval must stay well under it so an event-loop stall cannot let a live
    # service's key lapse.
    service_ttl: int = 30  # Presence key lifetime absent renewal
    service_renewal_interval: int = 10  # How often a connected service refreshes it

    # Redis key prefixes
    session_key_prefix: str = "mascope:session:"  # Session storage keys
    room_members_key_prefix: str = "mascope:rooms:members:"  # Users in room
    room_user_key_prefix: str = "mascope:rooms:user:"  # Rooms user is in
    service_key_prefix: str = "mascope:service:"  # Service presence keys

    def session_key(self, sid: str, namespace: str) -> str:
        """
        Build Redis key for session storage.

        :param sid: Socket.IO session ID
        :type sid: str
        :param namespace: Socket.IO namespace
        :type namespace: str
        :return: Redis key in format 'mascope:session:{namespace}:{sid}'
        :rtype: str
        """
        return f"{self.session_key_prefix}{namespace}:{sid}"

    def members_key(self, room_id: str) -> str:
        """
        Build Redis key for room members set.

        :param room_id: Room identifier (e.g., "batch-123")
        :return: Full Redis key (e.g., "mascope:rooms:members:batch-123")
        """
        return f"{self.room_members_key_prefix}{room_id}"

    def user_key(self, user_id: int) -> str:
        """
        Build Redis key for user's room set.

        :param user_id: User ID
        :type user_id: int
        :return: Full Redis key (e.g., "mascope:rooms:user:5")
        """
        return f"{self.room_user_key_prefix}{user_id}"

    def service_key(self, service_name: str) -> str:
        """
        Build Redis key for service presence.

        :param service_name: Service identifier (e.g., "file-converter")
        :type service_name: str
        :return: Full Redis key (e.g., "mascope:service:file-converter")
        :rtype: str
        """
        return f"{self.service_key_prefix}{service_name}"


# Global storage config instance
storage_config = SocketStorageConfig()
