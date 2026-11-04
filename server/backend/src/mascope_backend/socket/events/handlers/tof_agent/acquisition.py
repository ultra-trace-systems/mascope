from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.auth.decorators import socket_auth
from mascope_backend.socket.notifications import (
    UserNotification,
    emit_user_notification,
)
from mascope_runtime.logging import SENTRY_FINGERPRINT


instrument_acquisition_notification_process_id = gen_id(8)


def _instrument_of(event: str, acquisition_data: dict) -> str | None:
    """
    The instrument an acquisition event names, which is the room it notifies.

    An event with no instrument has no room to go to, and
    ``emit_user_notification`` refuses an emit with neither room nor user. So
    such an event logs one warning and is dropped. The warning is grouped per
    event in monitoring, so an agent that sends it on every acquisition stays
    one issue.

    :param event: The event's name, for the warning.
    :param acquisition_data: The event's payload.
    :return: The instrument, or None when the payload names none.
    """
    instrument = acquisition_data.get("instrument")
    if not instrument:
        runtime.logger.bind(
            **{SENTRY_FINGERPRINT: [f"tof-agent-acquisition-no-instrument:{event}"]}
        ).warning(
            f"TOF agent sent '{event}' with no instrument; there is no room to "
            "notify, so the event is dropped"
        )
        return None
    return instrument


@sio.event(namespace="/tof-agent")
@socket_auth(minimum_role="editor", service_name="tof-agent")
async def instrument_acquisition_started(sid: str, acquisition_data: dict):
    """Handle the start of an instrument acquisition.

    This event is triggered by TofAgent when an instrument acquisition starts, and it emits
    a user notification with the acquisitions details.

    :param sid: The session ID of the client.
    :type sid: str
    :param acquisition_data: A dictionary containing details about the acquisition,
                            including instrument name, filename, and polarity.
    :type acquisition_data: dict
    """
    instrument = _instrument_of("instrument_acquisition_started", acquisition_data)
    if instrument is None:
        return
    filename = acquisition_data["filename"]
    polarity = acquisition_data["polarity"]

    instrument_acquisition_notification = UserNotification(
        process_id=instrument_acquisition_notification_process_id,
        type="instrument_acquisition",
        status="pending",
        message=f"Acquisition {filename} ({instrument})",
        progress=0,
        data={"instrument": instrument, "filename": filename, "polarity": polarity},
    )

    await emit_user_notification(
        notification=instrument_acquisition_notification, room_id=instrument
    )


@sio.event(namespace="/tof-agent")
@socket_auth(minimum_role="editor", service_name="tof-agent")
async def instrument_acquisition_progress(sid: str, acquisition_data: dict):
    """Handle the progress of an instrument acquisition.

    This event is triggered by TofAgent to update the progress of an ongoing instrument
    acquisition.

    :param sid: The session ID of the client.
    :type sid: str
    :param acquisition_data: A dictionary containing details about the acquisition,
                            including instrument name, filename, and progress percentage.
    :type acquisition_data: dict
    """

    instrument = _instrument_of("instrument_acquisition_progress", acquisition_data)
    if instrument is None:
        return
    filename = acquisition_data["filename"]
    progress = acquisition_data["progress"]

    instrument_acquisition_notification = UserNotification(
        process_id=instrument_acquisition_notification_process_id,
        type="instrument_acquisition",
        status="pending",
        message=f"Acquisition {filename} ({instrument})",
        progress=progress,
        data={"instrument": instrument, "filename": filename},
    )

    await emit_user_notification(
        notification=instrument_acquisition_notification, room_id=instrument
    )


@sio.event(namespace="/tof-agent")
@socket_auth(minimum_role="editor", service_name="tof-agent")
async def instrument_acquisition_finished(sid: str, acquisition_data: dict):
    """Handle the completion of an instrument acquisition.

    This event is triggered by TofAgent when an instrument acquisition is completed,
    and it emits a user notification with the acquisition details.

    :param sid: The session ID of the client.
    :type sid: str
    :param acquisition_data: A dictionary containing details about the acquisition,
                            including instrument name, filename, and progress percentage.
    :type acquisition_data: dict
    """

    instrument = _instrument_of("instrument_acquisition_finished", acquisition_data)
    if instrument is None:
        return
    filename = acquisition_data["filename"]
    progress = acquisition_data["progress"]

    instrument_acquisition_notification = UserNotification(
        process_id=instrument_acquisition_notification_process_id,
        type="instrument_acquisition",
        status="success",
        message=f"Acquisition of file {filename} completed, instrument {instrument}.",
        progress=progress,
        data={"instrument": instrument, "filename": filename},
    )

    await emit_user_notification(
        notification=instrument_acquisition_notification, room_id=instrument
    )
