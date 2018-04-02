import sys
import threading
import collections
import queue
from network import *

IPPROTO_RDT = 0xfe

class RDTSocket(StreamSocket):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Other initialization here

    def bind(self, port):
        pass

    def listen(self):
        pass

    def accept(self):
        pass

    def connect(self, addr):
        pass

    def send(self, data):
        pass

class RDTProtocol(Protocol):
    PROTO_ID = IPPROTO_RDT
    SOCKET_CLS = RDTSocket

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Other initialization here

    def input(self, seg, rhost):
        pass
