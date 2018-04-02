from network import *

class SampleDatagramSocket(DatagramSocket):
    """Socket class for SampleDatagramProtocol"""

    def input(self, data, src):
        self.deliver(data, src)

    def sendto(self, msg, dst):
        # SDP has no header; segment = message
        self.output(msg, dst)

class SampleDatagramProtocol(Protocol):
    """
    Sample unreliable datagram protocol

    This class is intended only to demonstrate how to subclass Protocol,
    and it is of no practical use.  Rather than do any multiplexing, it creates
    a single datagram socket (per host) and passes it every datagram which
    arrives for this protocol.
    """

    PROTO_ID = 99
    SOCKET_CLS = SampleDatagramSocket

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sock = super().socket()

    def socket(self):
        return self.sock

    def input(self, seg, src):
        self.sock.input(seg, src)
