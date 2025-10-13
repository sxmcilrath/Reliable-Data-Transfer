from network import Protocol, StreamSocket
import threading
import sys
import os
import random

# Reserved protocol number for experiments; see RFC 3692
IPPROTO_RDT = 0xfe


class RDTSocket(StreamSocket):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Other initialization here
        self.is_connected = False
        self.is_bound = False
        self.port = None
        self.lock = threading.Lock()

    def bind(self, port):

        self.lock.acquire()
        #if in use by another system
        if(self.proto.bound_ports[port]):
            raise StreamSocket.AddressInUse
        #check if alr connected
        if(self.is_connected):
            raise StreamSocket.AlreadyConnected
        
        #set fields and bind
        self.port = port
        self.is_bound = True
        StreamSocket.bind(port)

        self.lock.release()

        

    def listen(self):
        self.lock.acquire()

        #err checking
        if(not self.is_bound):
            raise StreamSocket.NotBound

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
        self.bound_ports = {} #bool dictionary if ports are being used
        self.lock = threading.Lock()

    def input(self, seg, rhost):
        pass
