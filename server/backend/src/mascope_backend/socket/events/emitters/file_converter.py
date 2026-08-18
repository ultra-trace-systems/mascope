from mascope_backend.runtime import runtime
from mascope_backend.socket import sio
from mascope_backend.socket.emitter import event_emitter


# The room every accepted converter connection joins at connect (see the
# /file-converter connect handler). Emitting to the room reaches exactly the
# authenticated converter connections, on whichever worker holds them - the
# client manager keeps membership current across connects and disconnects - and
# delivers to no one when no converter is connected, without ever broadcasting
# to arbitrary sockets on the namespace.
FILE_CONVERTER_ROOM = "file-converter"


@event_emitter.on("file-converter.auth")
async def send_file_context_to_converter(data: dict):
    """
    Handle file-converter.auth events, emit socket events to file converter service.

    The payload carries the acting user's access token, so it is delivered to
    the converter room (authenticated connections only), never to arbitrary
    sockets on the namespace.

    :param data: Dict containing file and user context
    :type data: dict
    """
    runtime.logger.debug(f"Emitting file_context for {data.get('filename')}")
    await sio.emit(
        "file_context", data, namespace="/file-converter", room=FILE_CONVERTER_ROOM
    )


@event_emitter.on("file-converter.peak_detection_request")
async def send_peak_detection_request_to_converter(data: dict):
    """
    Send a peak detection request to the file converter service via Socket.IO.

    Emitted when a user manually triggers peak detection from the backend API.
    The file converter will handle the actual compute_peaks call. The payload
    carries the acting user's access token, so it is delivered to the converter
    room (authenticated connections only), never to arbitrary sockets on the
    namespace.

    :param data: Dict containing filename, sample_file_id, user_id, access_token
    :type data: dict
    """
    runtime.logger.debug(f"Emitting peak_detection_request for {data.get('filename')}")
    await sio.emit(
        "peak_detection_request",
        data,
        namespace="/file-converter",
        room=FILE_CONVERTER_ROOM,
    )
