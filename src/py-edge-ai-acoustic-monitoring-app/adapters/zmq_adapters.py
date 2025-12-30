"""
Implements ZeroMQ adapters to facilitate distributed producer-consumer patterns.

This module provides wrappers that mimic the standard Python `queue.Queue` interface
using ZeroMQ sockets. This allows the ListenerThread (Producer) and ConsumerThread
(Consumer) to run in separate system processes or even different physical machines
while maintaining the same code logic.

Key features:
1. ZmqProducerQueue: Uses a ZMQ_PUSH socket to serialize and send audio chunks.
2. ZmqConsumerQueue: Uses a ZMQ_PULL socket to receive and deserialize audio data.
3. Compatibility: Implements .put() and .get() to remain interchangeable with local queues.

Author: Daniel Collier
GitHub: https://github.com/danielfcollier
Year: 2025
"""

import pickle
import queue

import zmq

from ..settings import get_settings

settings = get_settings()


class ZmqProducerQueue:
    """
    Acts as a 'duck-typed' replacement for queue.Queue, pushing data to a ZMQ socket.

    This class allows a ListenerThread to operate as a standalone producer process
    by binding a PUSH socket to a specified host and port.
    """

    def __init__(self, host: str = settings.ZMQ_SOCKET_HOST, port: int = settings.ZMQ_SOCKET_PORT):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PUSH)
        self.socket.bind(f"tcp://{host}:{port}")

    def put_nowait(self, item):
        """
        Serializes the item (typically a tuple of audio data and timestamp) and sends it.
        """
        data = pickle.dumps(item)
        self.socket.send(data)

    def put(self, item):
        self.put_nowait(item)


class ZmqConsumerQueue:
    """
    Acts as a 'duck-typed' replacement for queue.Queue, pulling data from a ZMQ socket.

    This class allows a ConsumerThread to operate as a standalone analysis process
    by connecting a PULL socket to the producer's host and port.
    """

    def __init__(self, host: str = settings.ZMQ_SOCKET_HOST, port: int = settings.ZMQ_SOCKET_PORT):
        self.context = zmq.Context()
        self.socket = self.context.socket(zmq.PULL)
        self.socket.connect(f"tcp://{host}:{port}")

    def get(self, timeout_seconds=None):
        """
        Blocking get with timeout to mimic the behavior of queue.Queue.get().

        :param timeout_seconds: Timeout for blocking retrieval in seconds.
        :raises queue.Empty: If no data is received within the timeout period.
        """
        timeout_milliseconds = timeout_seconds * 1000 if timeout_seconds else None

        if self.socket.poll(timeout=timeout_milliseconds):
            data = self.socket.recv()
            return pickle.loads(data)
        else:
            raise queue.Empty
