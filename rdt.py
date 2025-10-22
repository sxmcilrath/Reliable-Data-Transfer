from network import Protocol, StreamSocket
import threading
import queue
import struct
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

        #segment vars/flags
        self.seq_num = 0
        self.ack_num = 0
        self.syn_flag = 0
        self.ack_flag = 0
        self.fin_flag = 0

        self.port = None
        self.lock = threading.Lock()
        self.q = queue.Queue()

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
        self.proto.bound_ports[port] = self.event
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
        self.is_listening = True

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
        self.ack_num = 0 #TODO - should change this to be rand later
        self.syn_flag = 1

        #handle port not bound
        if(self.port == None):
            pass
        #port alr bound
        else:
            #assemble SYN segment
            flags = bytes([self.ack_flag | self.syn_flag << 1 | self.fin_flag << 2])
            #precheck: int = bytearray([self.port, self.rport, self.seq_num, self.ack_num, flags]) #no data in control msg
            precheck = struct.pack('!HHIIB', self.port, self.rport, self.seq_num, self.ack_num, flags)
            checksum = get_checksum(precheck)
            syn_seg = struct.pack('!HHIIBH', self.port, self.rport, self.seq_num, self.ack_num, flags, checksum)
 
            #keep sending SYN until SYNACK is recieved
            segment = None
            while(segment == None):
                StreamSocket.send(syn_seg)
                segment = self.q.get(timeout=1) #TODO = change to proper timeout
                
                #verify no bits flipped
                err = verify_checksum(segment)
                if(err):
                    segment == None
                
                #verify ack 
                _, _, _, ack_num, _, _= struct.unpack('!HHIIBH', segment)
                if(ack_num != self.seq_num + 1):
                    segment == None
                
            

            #assemble ACK segment
            self.syn_flag = 0
            self.ack_flag = 1
            
            flags = bytes([self.ack_flag | self.syn_flag << 1 | self.fin_flag << 2])
            precheck = bytearray([self.port, self.rport, self.seq_num, self.ack_num, flags]) #no data in control msg
            checksum = get_checksum(precheck)
            ack_seg = bytearray([precheck, checksum])

            #keep sending until proper ACK retrieved 
            segment = None
            while(segment == None):
                StreamSocket.send(ack_seg)
                segment  = self.q.get(timeout=1) #TODO = change to proper timeout 

        

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
        

        flags = bytes([self.ack_flag | self.syn_flag << 1 | self.fin_flag << 2])
        precheck: int = bytearray([self.port, self.rport, self.seq_num, self.ack_num, flags, data])

        #send
        StreamSocket.send(bytearray([precheck, checksum]))
        pass



class RDTProtocol(Protocol):
    PROTO_ID = IPPROTO_RDT
    SOCKET_CLS = RDTSocket

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Other initialization here
        self.bound_ports = {} #bool dictionary if ports are being used port # -> threading.Event
        self.lock = threading.Lock()

    def input(self, seg, rhost):
        pass
