from fastapi import APIRouter, Depends, Query

from mascope_backend.api.lib.api_features import api_route
from mascope_backend.api.new.auth.dependencies import guest_user
from mascope_backend.api.new.notifications.schemas import (
    GetNotificationsQueryParams,
    MarkNotificationsReadBody,
)
from mascope_backend.api.new.notifications.service import (
    list_notifications,
    mark_notifications_read,
)


notifications_router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@notifications_router.get("")
@api_route()
async def get_notifications_route(
    query_params: GetNotificationsQueryParams = Query(),
    user=Depends(guest_user),
):
    """List the signed-in person's kept notifications, unread first.

    :param query_params: Whether to include read ones, and how many to list.
    :param user: The signed-in person; only their own notifications are listed.
    :return: The notifications, with how many are unread.
    """
    return await list_notifications(
        user.id, include_read=query_params.include_read, limit=query_params.limit
    )


@notifications_router.post("/read")
@api_route()
async def mark_notifications_read_route(
    body: MarkNotificationsReadBody,
    user=Depends(guest_user),
):
    """Mark the signed-in person's notifications read.

    :param body: The notifications to mark, each as it was seen, or ``all``.
    :param user: The signed-in person; only their own notifications change.
    :return: How many were marked read, and which.
    """
    seen = (
        None
        if body.all
        else {row.notification_id: row.updated_utc for row in body.notifications}
    )
    return await mark_notifications_read(user.id, seen)
