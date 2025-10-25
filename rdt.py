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

        #TODO - refactor from booleans to state
        self.state = 'CLOSED' # Can be CLOSED, CONNECTING, CONNECTED, LISTENING

        #segment vars/flags
        self.seq_num = 0
        self.ack_num = 0
        self.syn_flag = 0
        self.ack_flag = 0
        self.fin_flag = 0

        self.port = None
        self.remote_addr = None
        self.parent = None
        self.lock = threading.Lock()
        self.q = queue.Queue() #TODO - change to seg_q
        self.conn_q = queue.Queue() #stores (socket, addr, port) items 

    def bind(self, port):
        #print(f"bind: attempting to bind {port}.")
        #lock
        self.proto.lock.acquire()

        #if in use by another system
        if(self.proto.bound_ports.get(port) != None):
            raise StreamSocket.AddressInUse
        #check if alr connected
        if(self.is_connected):
            raise StreamSocket.AlreadyConnected
        
        #set fields and bind
        self.port = port
        self.is_bound = True
        self.proto.bound_ports[port] = self
        
        #print(f"bind: successfully bound {port}.")
        #unlock
        self.proto.lock.release()

        

    def listen(self):
        self.lock.acquire()

        #err checking
        if(not self.is_bound):
            raise StreamSocket.NotBound
        if(self.is_connected):
            raise StreamSocket.AlreadyConnected
        
        self.is_listening = True

        self.lock.release()

    def accept(self):

        if(not self.is_listening):
            raise StreamSocket.NotListening
        
        new_sock, rhost, rport = self.conn_q.get() #check connection q for new conns
        return (new_sock, (rhost, rport)) 

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
            #ephem range - 49152 to 65535
            rand_port = random.randint(49152, 65535)
            while(self.proto.bound_ports[rand_port] != None):
                rand_port = random.randint(49152, 65535)
            self.proto.bound_ports[rand_port] = self


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
                StreamSocket.output(syn_seg, addr)

                #wait for SYNACK
                try:
                    segment = self.q.get(timeout=1) #TODO = change to proper timeout
                except queue.Empty: 
                    segment = None
                    continue
                
                #verify no bits flipped
                err = verify_checksum(segment)
                if(err):
                    segment = None
                
                #verify ack 
                _, _, seq_num, ack_num, flags, _= struct.unpack('!HHIIBH', segment)

                syn_flag = (flags >> 1) & 1
                if((ack_num != self.seq_num + 1) or (syn_flag != 1)):
                    segment = None
                

            #assemble ACK segment
            self.syn_flag = 0
            self.ack_flag = 1
            self.ack_num = seq_num + 1
            
            flags = bytes([self.ack_flag | self.syn_flag << 1 | self.fin_flag << 2])
            precheck = struct.pack('!HHIIB', self.port, self.rport, self.seq_num, self.ack_num, flags)
            checksum = get_checksum(precheck)
            ack_seg = struct.pack('!HHIIBH', self.port, self.rport, self.seq_num, self.ack_num, flags, checksum)

            #send ACK and mark connected
            StreamSocket.output(ack_seg, addr)
            self.is_connected = True

        

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
        self.connecting_sockets = {} #dict stores sockets waiting for S SA A handshake ACK (remip, remport, sport) -> socket
        self.lock = threading.Lock()
        
    def input(self, seg: struct, rhost):

        ##handle the SYN case 
        rport, dport, seq_num, ack_num, flags, checksum, data = seg.unpack('!HHIIBH')

        if((flags >> 1 & 1) == 1): ##SYN flag set 
            #check if specified port is listening 
            dest_sock: RDTSocket = self.bound_ports.get(seg[2:4])

            #raise exception if port isn't bound or isn't listening 
            if(dest_sock == None):
                print(f"RDT Input: port {seg[2:4]} is not bound")
                raise Exception
            if(not dest_sock.is_listening):
                raise StreamSocket.NotListening
            
            #create new socket for conn
            new_sock: RDTSocket = self.socket()
            new_sock.bind(random.randint(49152, 65535))
            self.connecting_sockets[(rhost, rport, dport)] = new_sock

            #send SYN ACK
            pre_check = struct.pack('!HHIIB', new_sock.port, rport, random.randint(0,10000), seq_num + 1)
            checksum = get_checksum(pre_check)
            ack_seg = struct.pack('!HHIIBH', new_sock.port, rport, random.randint(0,10000), seq_num + 1, checksum)

            new_sock.output(ack_seg, (rhost, rport))
        
        
        #behavior for handshake ACK recieved
        conn_sock: RDTSocket = self.connecting_sockets.get((rhost, rport, dport))

        if((flags & 1 == 1) and conn_sock != None): # ACK flag set and matching connection in progress
            #need to place connection in the parents queue 
            conn_sock.state = 'CONNECTED' #set child socket status
            parent_sock: RDTSocket = self.bound_ports[conn_sock.parent]
            parent_sock.conn_q.put((conn_sock, rhost, rport))

        pass
