import inspect
from functools import wraps
from typing import Callable

from fastapi import params
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from rich.pretty import pretty_repr

from mascope_backend.api.lib.exceptions.api_exceptions import (
    ApiException,
    api_e_response_json,
    compose_user_message,
    handle_exception,
    process_exception,
)
from mascope_backend.api.lib.utils import beautify_func_name, handle_reloads
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket.notifications import (
    UserNotification,
    handle_notifications,
)


def _binds_auth_dependency(param: inspect.Parameter) -> bool:
    """
    Whether a route parameter binds a FastAPI dependency, in either of the
    styles FastAPI accepts: a ``Depends(...)`` default value
    (``user=Depends(guest_user)``) or ``Annotated`` metadata
    (``user: Annotated[User, Depends(guest_user)]``).

    Note: the ``Annotated`` check inspects the evaluated annotation object;
    under ``from __future__ import annotations`` (string annotations) it cannot
    see the metadata and the guard rejects the route. That failure is loud and
    at import time - fail closed, never silently unauthenticated.

    :param param: The ``user`` parameter from the route handler's signature.
    :return: ``True`` if an auth dependency is bound to the parameter.
    """
    if isinstance(param.default, params.Depends):
        return True
    return any(
        isinstance(meta, params.Depends)
        for meta in getattr(param.annotation, "__metadata__", ())
    )


def api_controller():
    """
    A decorator for controller functions to handle exceptions and standardize error responses.

    This decorator wraps a controller function, catches any exceptions thrown during its execution.
    If an ApiException is caught (meaning that some other controller was called and it has already
    raised the processed ApiException), it is re-raised to be handled by FastAPI's exception handlers.
    For non-processed exceptions, an ApiException is raised with a custom or default error
    message, providing additional context about where the error occurred.

    :return: The decorated controller function wrapped in a try-except block for exception handling
    :rtype: Callable
    """

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except ApiException as e:
                # Handle already processed exceptions
                if e.status_code == 200:
                    # General warning
                    context_message = (
                        f"Warning during {beautify_func_name(func.__name__)}"
                    )
                elif e.status_code == 207:
                    # Batch multi-status response, partial success
                    context_message = (
                        f"Partially succeeded to {beautify_func_name(func.__name__)}"
                    )
                else:
                    # Error cases
                    context_message = f"Failed to {beautify_func_name(func.__name__)}"

                user_message = compose_user_message(context_message, e.user_message)

                raise ApiException(user_message, e.tech_message, e.status_code) from e
            except Exception as e:
                context_message = f"Failed to {beautify_func_name(func.__name__)}"
                api_exc = process_exception(e, context_message)

                raise api_exc from e

        return wrapper

    return decorator


def api_route(
    status_code: int = 200,
    token_access: bool = False,
    public: bool = False,
):
    """
    A decorator for FastAPI route handlers that provides:
    1. Authentication enforcement by default (unless marked as public)
    2. Standardized response formatting
    3. Consistent error handling
    4. Optional token-based access for external service/agents/packages

    By default, all routes require authentication via auth user dependency injection.
    Routes must either:
    - Include auth user dependency (e.g., user=Depends(guest_user))
    - Or be explicitly marked as public with @api_route(public=True)

    Usage:
    Protected route (requires authentication):

        @router.get("/some-protected-path")
        @api_route()
        async def name_of_protected_route(user=Depends(guest_user)):
            ...

    Public route (no authentication):

        @router.get("/some-public-path")
        @api_route(public=True)
        async def name_of_public_route():
            ...

    Custom auth level with specific status code"
        @router.post("/some-admin-path")
        @api_route(status_code=201)
        async def name_of_admin_route(user=Depends(admin_user)):
            ...

    :param status_code: HTTP status code to be used for successful responses, defaults to 200.
    :type status_code: int, optional
    :param token_access: Flag to indicate if the route allows access via an access token (for Jupyter or external access).
                           If set to True, the endpoint is accessible via a Bearer token in addition to standard JWT authentication.
    :type token_access: bool, optional
    :param public: Flag to explicitly mark route as public (no auth)
    :type public: bool, optional
    :return: The wrapped route handler function with standardized response formatting and exception handling.
    :rtype: Callable
    """

    def decorator(func: Callable):
        # --- Configure route access token settings ---
        func.token_access = token_access

        # --- Verify route security - either must be public or bind an auth
        # dependency to a `user` parameter. Requiring an actual Depends(...)
        # (not merely a parameter literally named `user`) closes the gap where
        # a handler declares `user` without injecting auth and is silently
        # unauthenticated. ---
        if not public:
            signature = inspect.signature(func)
            user_param = signature.parameters.get("user")
            if user_param is None or not _binds_auth_dependency(user_param):
                runtime.logger.error("Please check the route definition")
                error_message = (
                    f"Configuration error in route '{func.__name__}'\n"
                    f"All routes must either:\n"
                    f"1. Bind an auth dependency to a `user` parameter, e.g. "
                    f"user=Depends(guest_user) or "
                    f"user: Annotated[User, Depends(guest_user)]\n"
                    f"2. Be explicitly marked as public with @api_route(public=True)\n"
                    f"A `user` parameter without a bound Depends(...) leaves the "
                    f"route unauthenticated; update the route definition accordingly."
                )
                raise ValueError(error_message)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # --- Log authenticated user information if available ---
            user = kwargs.get("user")
            if user:
                runtime.logger.trace(f"User:\n{pretty_repr(user.to_dict())}")

            try:
                # --- Execute the route handler
                result = await func(*args, **kwargs)

                # --- If result is file, return as-is ---
                if isinstance(result, FileResponse):
                    return result

                # --- Prepare response headers ---
                headers = {}
                if result is not None and "process_id" in result:
                    headers["Process-ID"] = result.pop("process_id")

                # --- Return formatted JSON response ---
                return JSONResponse(
                    status_code=status_code,
                    content=jsonable_encoder(result),
                    headers=headers,
                )
            except ApiException as e:
                return api_e_response_json(e)
            except Exception as e:
                # --- Handle generic exceptions ---
                context_message = f"Error in {beautify_func_name(func.__name__)}"
                return handle_exception(e, context_message)

        return wrapper

    return decorator


def api_controller_background_task(
    success_notification_rooms: list[str] | None = None,
    success_reload: list[tuple[str, str]] | None = None,
    error_notification_rooms: list[str] | None = None,
    error_reload: list[tuple[str, str]] | None = None,
) -> Callable:
    """
    Decorator for background task controllers that standardizes response and error handling, Socket.IO events.

    Transaction behavior:
    The decorator uses 'independent_transaction' controller's flag to manage Socket.IO communications:
    - When True: Emits notifications and reloads directly from this function
    - When False: Suppresses notifications/reloads and re-raises exceptions for parent handler
    - Special case: 'rematch_batch' function always emits reloads when parent_id exists
        TODO_notifications refactor this, looks like a hack

    The decorator supports two types of Socket.IO communications: (controlled by independent_transaction):
    1. Notifications: User-facing messages about task progress/status
       - Success: Always sent
       - Warnings: Reported unless the warning is re-raised to a parent
         handler (ApiException with status_code 200/207), which reports it
         instead; the re-raised case still emits the packet flagged `silent`,
         which only ends the task's progress bar in the frontend
       - Errors: Only sent for independent transactions
    2. Reloads: Data refresh signals for UI components
       - Success: Sent for independent transactions or rematch_batch
       - Warnings: Same as success
       - Errors: Only sent for independent transactions

    Usage:
        @api_controller_background_task(
            success_reload=[("match", "affected_sample_batch_ids")],
        )
        async def import_sample_items(
            sample_batch_id: str,
            **kwargs
            independent_transaction: bool = False,  # Controls notification behavior
        ) -> Dict[str, Any]:
            return {
                "_notification_data": {
                    "affected_sample_batch_ids": ["sample_batch_id1", ...]  # Will trigger reloads if independent
                }
            }

    :param success_notification_rooms: List of room keys for success user notifications, defaults to []
    :type success_notification_rooms: list[str], optional
    :param success_reload: list of tuples (event_name, room_key) for success UI reload notifications, defaults to []
    :type success_reload: list[tuple[str, str]], optional
    :param error_notification_rooms: list of room keys for error user notifications, defaults to []
    :type error_notification_rooms: list[str], optional
    :param error_reload: list of tuples (event_name, room_key) for error reloads, defaults to []
    :type error_reload: list[tuple[str, str]], optional
    :return: The decorated async function that add to async controller the notification
            and error handling logic
    :rtype: Callable
    """
    # Convert None to empty lists
    success_notification_rooms = (
        success_notification_rooms if success_notification_rooms is not None else []
    )
    success_reload = success_reload if success_reload is not None else []
    error_notification_rooms = (
        error_notification_rooms if error_notification_rooms is not None else []
    )
    error_reload = error_reload if error_reload is not None else []

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            independent_transaction = kwargs.get("independent_transaction", False)
            process_id = kwargs.get("process_id", gen_id(8))  # Generate if not provided
            parent_id = kwargs.get("parent_id", None)

            notification = UserNotification(
                process_id=process_id,
                parent_id=parent_id,
                progress=100,
                type=func.__name__,
                status="pending",
                message=f"{func.__name__.replace('_', ' ').title()} is processing.",
            )
            try:
                result = await func(*args, **kwargs)
                # Update notification on success
                notification.status = "success"
                notification.message = result.get("message") if result else None
                notification.data = (
                    result.get("_notification_data", None) if result else None
                )

                # Handle success user notifications
                await handle_notifications(
                    success_notification_rooms, notification, kwargs, result
                )
                # Handle success reload notifications
                # Emit reload events for remat_batch even if called as a part of remath_batches
                if independent_transaction or (
                    func.__name__ == "rematch_batch" and parent_id
                ):
                    await handle_reloads(
                        f"Success reload {func.__name__}",
                        success_reload,
                        kwargs,
                        result,
                    )

                return result
            except ApiException as e:
                if e.status_code in [200, 207]:
                    # Handle warning notifications - both general warnings (200) and multi-status (207)
                    notification.status = "warning"
                    notification.message = e.user_message
                    notification.error = {"detail": e.tech_message}

                    # A nested, dependent task's warning is re-raised below
                    # and reported again by whoever owns the process, so
                    # reporting it here as well filed one notification per
                    # nesting level per retry - fourteen for a single sample
                    # that would not calibrate (7 attempts x
                    # calibration_mz_fit + calibration_mz_calibrate_sample).
                    # The copy is suppressed under the same condition as the
                    # re-raise below, so no warning is both silenced here and
                    # never reported.
                    # The frontend never displayed these - it shows only
                    # parent-less notifications (stores/ui/notification.js) -
                    # but they did more than fill the log and the badge: on
                    # this path it is the only non-pending packet the child's
                    # process id sends, and that is what ends its progress bar.
                    # So the packet is flagged rather than dropped: a `silent`
                    # one still ends the progress, and the store keeps it out
                    # of the log, off the badge and away from the toast.
                    if not independent_transaction and parent_id:
                        notification.silent = True
                    await handle_notifications(
                        error_notification_rooms, notification, kwargs, None
                    )
                    if independent_transaction or (
                        func.__name__ == "rematch_batch" and parent_id
                    ):
                        await handle_reloads(
                            f"ApiException reload (status {e.status_code}) {func.__name__}",
                            error_reload,
                            kwargs,
                            e.tech_message,
                        )

                    if not independent_transaction and parent_id:
                        # Re-raise warning exceptions to be caught in parent handler
                        raise ApiException(
                            e.user_message, e.tech_message, e.status_code
                        )
                else:
                    # Update the payload with ApiException error details
                    notification.status = "error"
                    notification.message = compose_user_message(
                        f"Failed to {beautify_func_name(func.__name__)}",
                        e.user_message,
                    )
                    notification.error = {"detail": e.tech_message}
                    #  Emit error user notifications only if this is an independent transaction
                    if independent_transaction:
                        # NOTE: for the error_notification_rooms the user_id should be provided
                        # in the controller kwargs, since the result is not available
                        await handle_notifications(
                            error_notification_rooms, notification, kwargs, None
                        )
                        await handle_reloads(
                            context=f"ApiException reload (status {e.status_code}) {func.__name__}",
                            reload_events=error_reload,
                            kwargs=kwargs,
                            result=e.tech_message,
                        )
                    # If not an independent transaction, re-raise the ApiException
                    else:
                        # Handle already processed exceptions
                        context_message = (
                            f"Failed to {beautify_func_name(func.__name__)}"
                        )
                        user_message = compose_user_message(
                            context_message, e.user_message
                        )
                        raise ApiException(
                            user_message, e.tech_message, e.status_code
                        ) from e

            except Exception as e:
                context_message = f"Failed to {beautify_func_name(func.__name__)}"
                api_exc = process_exception(e, context_message)

                #  Emit error user notifications only if this is an independent transaction
                if independent_transaction:
                    # Update the payload with error details
                    notification.status = "error"
                    notification.message = api_exc.user_message
                    notification.error = {"detail": api_exc.tech_message}
                    # NOTE: for the error_notification_rooms the user_id should be provided
                    # in the controller kwargs, since the result is not available
                    await handle_notifications(
                        error_notification_rooms, notification, kwargs, None
                    )
                    await handle_reloads(
                        f"Unhandled Exception reload {func.__name__}",
                        error_reload,
                        kwargs,
                        None,
                    )
                # If not an independent transaction, re-raise the ApiException
                else:
                    raise api_exc from e

        return wrapper

    return decorator
