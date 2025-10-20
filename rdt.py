from network import Protocol, StreamSocket
import threading
import sys
import os
import random

# Reserved protocol number for experiments; see RFC 3692
IPPROTO_RDT = 0xfe
Q_SIZE = 10

class RDTSocket(StreamSocket):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Other initialization here
        
        #status trackers
        self.is_connected = False
        self.is_bound = False
        self.is_listening = False

        #segment vars
        self.seq_num = 0
        self.ack_num = 0

        self.port = None
        self.lock = threading.Lock()

    def bind(self, port):

        #lock
        self.proto.lock.acquire()

        #if in use by another system
        if(self.proto.bound_ports[port]):
            raise StreamSocket.AddressInUse
        #check if alr connected
        if(self.is_connected):
            raise StreamSocket.AlreadyConnected
        
        #set fields and bind
        self.port = port
        self.is_bound = True
        self.proto.bound_ports[port] = True
        StreamSocket.bind(port)

        #unlock
        self.proto.lock.release()

        

    def listen(self):
        self.lock.acquire()

        #err checking
        if(not self.is_bound):
            raise StreamSocket.NotBound
        if(self.is_connected):
            raise StreamSocket.AlreadyConnected
        
        StreamSocket.listen()

        self.lock.release()

    def accept(self):
        if(not self.is_listening):
            raise StreamSocket.NotListening
        
        conn_sock, conn_addr = StreamSocket.accept()
        return conn_sock, conn_addr

    def connect(self, addr):
        #exceptions
        if(self.is_connected):
            raise StreamSocket.AlreadyConnected
        if(self.is_listening):
            raise StreamSocket.AlreadyListening
        
        #reset instance vars
        self.seq_num = 0
        self.ack_num = 0

        #handle port not bound
        if(self.port == None):
            pass
        #port alr bound
        else:
            StreamSocket.connect(addr)
        

    def send(self, data):

        '''
        We need
        - source prt
        - dest prt 
        - seq # - could be instance var 
        - ack # - could be instance var
        - checksum -> calc as last step
        - data - given as param
        '''
        #check if connected
        if(not self.is_connected):
            raise StreamSocket.NotConnected
        

        #assemble segment pre check

        flags = bytes([self.ack_flag | self.syn_flag << 1 | self.fin_flag << 2])
        precheck: int = bytearray([self.port, self.rport, self.seq_num, self.ack_num, flags, data])

        #perform checksum
        ##I need to divide into 16 bit segments and then add them 
        ##shift bytes by 16 and & with 0xFF
        total = bin(0)
        for i in range(0, len(precheck) * 8, 16): #want to range over the number of bits
            word = (precheck >> i) & 0xFFFF 
            total += word
            total = (total & 0xFFFF) + (total >> 16) #add the overflow
        #add overflow
        checksum = ~checksum & 0xFFFF #take complement and ensure only 16 bits

        #send
        StreamSocket.send(bytearray([checksum, precheck]))
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
