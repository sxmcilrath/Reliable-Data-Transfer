#!/usr/bin/env python3

from mynet import StreamSocket

class UDTSocket(StreamSocket):
    PROTO_ID = 0xFD

    def __init__(self, host):
        super().__init__(host)
        self.lport = None
        self.raddr = None

    def bind(self, port):
        self.lport = port

    def connect(self, addr):
        if self.lport is None:
            self.lport = self.host.random_port()
        self.raddr = addr

    def send(self, msg):
        self.output(msg, self.raddr)

    def input(self, seg, addr):
        self.deliver(seg)
