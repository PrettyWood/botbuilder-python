# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import platform
from collections import Counter
from http import HTTPStatus
from datetime import datetime
from logging import Logger
from typing import Dict, List

from botbuilder.core import Bot
from botbuilder.schema import Activity, Attachment, ResourceResponse
from botbuilder.streaming import (
    RequestHandler,
    ReceiveRequest,
    StreamingRequest,
    StreamingResponse,
    __title__,
    __version__,
)
from botbuilder.streaming.transport.web_socket import WebSocket, WebSocketServer

from .streaming_activity_processor import StreamingActivityProcessor
from .version_info import VersionInfo


class StreamingRequestHandler(RequestHandler):
    def __init__(
        self,
        bot: Bot,
        activity_processor: StreamingActivityProcessor,
        web_socket: WebSocket,
        logger: Logger = None,
    ):
        if not bot:
            raise TypeError(f"'bot: {bot.__class__.__name__}' argument can't be None")
        if not activity_processor:
            raise TypeError(
                f"'activity_processor: {activity_processor.__class__.__name__}' argument can't be None"
            )

        self._bot = bot
        self._activity_processor = activity_processor
        self._logger = logger
        self._conversations: Dict[str, datetime] = {}
        self._user_agent = StreamingRequestHandler._get_user_agent()
        self._server = WebSocketServer(web_socket, self)
        self._server_is_connected = True
        self._server.disconnected_event_handler = self._server_disconnected
        self._service_url: str = None

    @property
    def service_url(self) -> str:
        return self._service_url

    async def listen(self):
        await self._server.start()
        # TODO: log it

    def has_conversation(self, conversation_id: str) -> bool:
        return conversation_id in self._conversations

    def conversation_added_time(self, conversation_id: str) -> datetime:
        added_time = self._conversations.get(conversation_id)

        if not added_time:
            added_time = datetime.min

        return added_time

    def forget_conversation(self, conversation_id: str):
        del self._conversations[conversation_id]

    async def process_request(
        self, request: ReceiveRequest, logger: Logger, context: object
    ) -> StreamingResponse:
        response = StreamingResponse()

        # We accept all POSTs regardless of path, but anything else requires special treatment.
        if not request.verb == StreamingRequest.POST:
            return self._handle_custom_paths()

        # Convert the StreamingRequest into an activity the adapter can understand.
        try:
            body = request.read_body_as_str()
        except Exception as error:
            response.status_code = int(HTTPStatus.BAD_REQUEST)
            # TODO: log error

            return response

        try:
            # TODO: validate if should use deserialize or from_dict
            activity: Activity = Activity.deserialize(body)

            # All activities received by this StreamingRequestHandler will originate from the same channel, but we won't
            # know what that channel is until we've received the first request.
            if not self.service_url:
                self._service_url = activity.service_url

            # If this is the first time the handler has seen this conversation it needs to be added to the dictionary so
            # the adapter is able to route requests to the correct handler.
            if not self.has_conversation(activity.conversation.id):
                self._conversations[activity.conversation.id] = datetime.now()

            """
            Any content sent as part of a StreamingRequest, including the request body
            and inline attachments, appear as streams added to the same collection. The first
            stream of any request will be the body, which is parsed and passed into this method
            as the first argument, 'body'. Any additional streams are inline attachments that need
            to be iterated over and added to the Activity as attachments to be sent to the Bot.
            """

            if len(request.streams) > 1:
                stream_attachments = [
                    Attachment(content_type=stream.content_type, content=stream.stream)
                    for stream in request.streams
                ]

                if activity.attachments:
                    activity.attachments += stream_attachments
                else:
                    activity.attachments = stream_attachments

            # Now that the request has been converted into an activity we can send it to the adapter.
            adapter_response = await self._activity_processor.process_streaming_activity(
                activity, self._bot.on_turn
            )

            # Now we convert the invokeResponse returned by the adapter into a StreamingResponse we can send back
            # to the channel.
            if not adapter_response:
                response.status_code = int(HTTPStatus.OK)
            else:
                response.status_code = adapter_response.status
                if adapter_response.body:
                    response.set_body(adapter_response.body)

        except Exception as error:
            response.status_code = int(HTTPStatus.INTERNAL_SERVER_ERROR)
            response.set_body(str(error))
            # TODO: log error

        return response

    async def send_activity(self, activity: Activity) -> ResourceResponse:
        if activity.reply_to_id:
            request_path = (
                f"/v3/conversations/{activity.conversation.id if activity.conversation else ''}/"
                f"activities/{activity. reply_to_id}"
            )
        else:
            request_path = f"/v3/conversations/{activity.conversation.id if activity.conversation else ''}/activities"

        stream_attachments = self.updat

    @staticmethod
    def _get_user_agent() -> str:
        package_user_agent = f"{__title__}/{__version__}"
        uname = platform.uname()
        os_version = f"{uname.machine}-{uname.system}-{uname.version}"
        py_version = f"Python,Version={platform.python_version()}"
        platform_user_agent = f"({os_version}; {py_version})"
        user_agent = f"{package_user_agent} {platform_user_agent}"
        return user_agent

    async def update_attachment_streams(self, activity: Activity) -> List[object]:
        if not activity or not activity.attachments:
            return None

        def validate_int_list(obj: object) -> bool:
            if not isinstance(obj, list):
                return False

            return all(isinstance(element, int) for element in obj)

        stream_attachments = [
            attachment
            for attachment in activity.attachments
            if validate_int_list(attachment.content)
        ]

        if stream_attachments:
            activity.attachments = [
                attachment
                for attachment in activity.attachments
                if not validate_int_list(attachment.content)
            ]

    def _handle_custom_paths(
        self, request: ReceiveRequest, response: StreamingResponse
    ) -> StreamingResponse:
        if not request or not request.verb or not request.path:
            response.status_code = int(HTTPStatus.BAD_REQUEST)
            # TODO: log error

            return response

        if request.verb == StreamingRequest.GET and request.path == "/api/version":
            response.status_code = int(HTTPStatus.OK)
            response.set_body(VersionInfo(user_agent=self._user_agent))

            return response

        return None
