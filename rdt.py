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
        self.parent: RDTSocket = None
        self.lock = threading.Lock()
        self.q = queue.Queue() #TODO - change to seg_q
        self.conn_q = queue.Queue() #stores (socket, addr, port) items 

    def bind(self, port):
        print(f"bind: attempting to bind {port}.")
        #lock
        self.proto.lock.acquire()
        #if in use by another system
        if(self.proto.bound_ports.get(port) != None):
            raise StreamSocket.AddressInUse
        #check if alr connected
        if(self.state == 'CONNECTED'):
            raise StreamSocket.AlreadyConnected
        
        #set fields and bind
        self.proto.bound_ports[port] = self
        self.proto.lock.release()

        self.lock.acquire()
        self.port = port
        self.state = "BOUND"
        self.lock.release()
        

    def listen(self):
        
        print("listen: arrived")
        #err checking
        if(self.port == None):
            raise StreamSocket.NotBound
        if(self.state == 'CONNECTED'):
            raise StreamSocket.AlreadyConnected
        
        #add to protocol server sockets and change state
        self.proto.lock.acquire()
        self.proto.server_sockets[self.port] = self
        self.proto.lock.release()

        self.lock.acquire()
        self.state = 'LISTENING'
        print(f"listen: listening on {self.port}")
        self.lock.release()

    def accept(self):

        print(f'accept: arrived at port - ({self.port})')
        if(self.state != 'LISTENING'):
            raise StreamSocket.NotListening
        
        print('accept: checking q for connection')
        new_sock, rhost, rport = self.conn_q.get(5) #check connection q for new conns
        print('accept: connection accepted')
        
        #set state
        new_sock.lock.acquire()
        new_sock.state = 'CONNECTED'
        new_sock.lock.release()

        return (new_sock, (rhost, rport)) #return conn
        

    def connect(self, addr):
        print(f"connect: self port - {self.port} arrived w/ address: {addr}")
        #exceptions
        if(self.is_connected):
            raise StreamSocket.AlreadyConnected
        if(self.is_listening):
            raise StreamSocket.AlreadyListening
        
        #reset instance vars
        print('connect: waiting for sock lock')
        self.lock.acquire()
        print('connect: sock lock acquired')
        self.remote_addr = addr
        print('connect: sock lock released')
        self.lock.release()

        #handle port not bound
        if(self.port == None):
            #ephem range - 49152 to 65535
            rand_port = random.randint(49152, 65535)
            while(self.proto.bound_ports.get(rand_port) != None):
                rand_port = random.randint(49152, 65535)
            self.port = rand_port
        
        print(f"connect: self port - {self.port}")
        #assemble SYN segment
        flags = 0 | 1 << 1 | 0 << 2
        seq_num = random.randint(0,4294967295)
        precheck = struct.pack('!HHIIB', self.port, self.remote_addr[1], seq_num, 0, flags)
        checksum = get_checksum(precheck)
        syn_seg = struct.pack('!HHIIBH', self.port, self.remote_addr[1], seq_num, 0, flags, checksum)

        #keep sending SYN until SYNACK is recieved
        segment = None
        while(segment == None):
            print('connect: sending SYN')
            print(f'connect: storing connection - ({self.port}, {addr[0]}, {addr[1]})')
            self.proto.connecting_socks[(self.port, addr[0], addr[1])] = self #store connection in connecting table
            self.state = 'CONNECTING'
            self.output(syn_seg, addr[0])

            #wait for SYNACK
            try:
                segment = self.q.get(timeout=10) #TODO = change to proper timeout
            except queue.Empty: 
                segment = None
                continue
            
            print("connect: Something put in q!")
            rport, _, seq_num, ack_num, flags, _= struct.unpack('!HHIIBH', segment)

            #TODO - verify seq number

            #if not SYN ACK
            if(flags != 3):
                segment = None
                print("connect: incorrect response")
            else:
                print("connect: ACK recieved!")
            

        #assemble ACK segment        
        flags = 1 | 0 << 1 | 0 << 2 #just doing this for myself lol
        precheck = struct.pack('!HHIIB', self.port, self.remote_addr[1], 0, seq_num + 1, flags)
        checksum = get_checksum(precheck)
        ack_seg = struct.pack('!HHIIBH', self.port, self.remote_addr[1], 0, seq_num + 1, flags, checksum)

        #move from connecting to connected
        self.proto.lock.acquire()
        self.proto.connecting_socks.pop((self.port, addr[0], addr[1]))
        self.proto.connected_socks[(self.port, addr[0], addr[1])] = self
        self.proto.lock.release()

        self.state = "CONNECTED"

        #send ACK and mark connected
        #TODO - what happens is the ACK gets dropped?
        print("connect: Sending ACK!")
        self.output(ack_seg, addr[0])

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
        print("send: arrived")
        #check if connected
        if(not self.is_connected):
            raise StreamSocket.NotConnected
        

        flags = bytes([self.ack_flag | self.syn_flag << 1 | self.fin_flag << 2])
        precheck: int = bytearray([self.port, self.rport, self.seq_num, self.ack_num, flags, data])

        #send
        StreamSocket.send(bytearray([precheck, checksum]))
        pass

    def handle_segment(self, seg, rhost):

        print("Handle Segment: arrived")
        rport, dport, seq_num, ack_num, flags, checksum = struct.unpack('!HHIIBH', seg)
        print(f"Handle Segment: rport-{rport} dport-{dport} seq-{seq_num} ack-num{ack_num} flags-{flags} checksum-{checksum}")
        
        #handle the SYN case 
        if(flags == 2): ##SYN flag set 
            print("Handle Segment: SYN Packet Recieved")
            #check if specified port is listening 
            dest_sock: RDTSocket = self.proto.bound_ports.get(dport)

            #raise exception if port isn't bound or isn't listening 
            if(dest_sock == None):
                print(f"Handle Segment: port {dport} is not bound")
                raise Exception
            
            print(f"Handle Segment: dest sock state: {dest_sock.state}")
            if(dest_sock.state != 'LISTENING'):
                raise StreamSocket.NotListening
            
            #create new socket for conn
            new_sock: RDTSocket = self.proto.socket()
            new_sock.port = self.port
            new_sock.parent = self
            new_sock.remote_addr = (rhost, rport)
            new_sock.state = 'CONNECTING'
            self.proto.connecting_socks[(self.port, rhost, rport)] = new_sock

            #send SYN ACK
            flags = 1 | 1 << 1 | 0 << 2
            pre_check = struct.pack('!HHIIB', new_sock.port, rport, random.randint(0,10000), seq_num + 1, flags)
            checksum = get_checksum(pre_check)
            synack_seg = struct.pack('!HHIIBH', new_sock.port, rport, random.randint(0,10000), seq_num + 1, flags, checksum)

            self.output(synack_seg, rhost)
            return
        
        #behavior for SYN ACK recieved
        if(flags == 3): 
            print("Handle Segment: SYN ACK Recieved")
            #pass off to connecting port q
            self.q.put(seg)
            return

        
        #behavior for handshake ACK recieved
        # self.proto.lock.acquire()
        # print(f"Handle Segment: Trying to get conn sock for - ({rhost}, {rport}, {dport})")
        # conn_sock: RDTSocket = self.proto.conn_socks.get((rhost, rport, dport))
        # print(f"Handle Segment: conn sock dict - {self.proto.conn_socks}")
        # self.proto.lock.release()
        
        # if(flags == 1 and conn_sock != None): # ACK flag set and matching connection in progress
        #     print('Handle Segment: ACK Received')
        #     #need to place connection in the parents queue 
        #     conn_sock.state = 'CONNECTED' #set child socket status
        #     parent_sock: RDTSocket = self.bound_ports[conn_sock.parent]
        #     parent_sock.conn_q.put((conn_sock, rhost, rport))

        #     #remove entry 
        #     self.proto.conn_socks.pop((rhost, rport, dport))
        # else:
        #     print(f"Handle Segment: not Handshake - {flags}")
        # return

class RDTProtocol(Protocol):
    PROTO_ID = IPPROTO_RDT
    SOCKET_CLS = RDTSocket
    

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Other initialization here
        self.bound_ports = {} #bool dictionary if ports are being used port # -> threading.Event
        self.connecting_socks = {} #dict stores sockets waiting for S SA A handshake ACK (remip, remport, sport) -> socket
        self.connected_socks = {} #dict stores sockets that have finished handshake
        self.server_sockets = {}
        self.lock = threading.Lock()
        
    #TODO - add locks
    #TODO - handle handshake ACKs 
    def input(self, seg, rhost):
        #we want to perform err check and then send to proper socket
        #extract fields
        try:
            print("Proto Input: arrived")
            rport, dport, seq_num, ack_num, flags, checksum = struct.unpack('!HHIIBH', seg)
            print(f"Proto Input: rport-{rport} dport-{dport} seq-{seq_num} ack-num{ack_num} flags-{flags} checksum-{checksum}")
        except: 
            print("Proto Input: err while unpacking segment")
            return
        #err check
        if(not verify_checksum(seg)):
            #what should be done? Resend ACK?
            pass
        
        #check if SYN
        if(flags == 2):
            print("Proto Input: SYN Recieved")
            #demux to listening ports
            dest_sock = self.server_sockets.get(dport)
            if(dest_sock == None):
                raise RDTSocket.NotBound()
            dest_sock.handle_segment(seg, rhost)
            return
        #check if SYNACK 
        elif(flags == 3):
            print(f"Proto Input: SYNACK - looking for key ({dport}, {rhost}, {rport})")
            print(f"Proto Input: Available keys: {list(self.connecting_socks.keys())}")
            dest_sock = self.connecting_socks.get((dport, rhost, rport))

            if(dest_sock == None):
                print(f"Proto Input: Conn not found - ({dport}, {rhost}, {rport})")
                return

            print(f'Proto Input: placing SYNACK on {dest_sock.port}\'s q ')
            dest_sock.q.put(seg)
            return



        #check if handshake ACK 
        print(f"Proto Input: ACK - looking for key ({dport}, {rhost}, {rport})")
        print(f"Proto Input: Available connecting keys: {list(self.connecting_socks.keys())}")
        dest_sock: RDTSocket = self.connecting_socks.pop((dport, rhost, rport), None)
        if(flags == 1 and dest_sock != None):
            print("Proto Input: handshake ACK found!")
            #place on accepting queue 
            self.connected_socks[(dport, rhost, rport)] = dest_sock
            dest_sock.parent.conn_q.put((dest_sock, rhost, rport))
        else:
            print(f"Proto Input: no connection found for ({dport}, {rhost}, {rport}), flags={flags}")

        
        #demux
        # dest_sock = self.connected_socks[(dport, rhost, rport)]
        # # if( dest_sock == None): #check if valid port
        # #     raise RDTSocket.NotBound()
        # dest_sock.handle_segment(seg, rhost)
        
##HELPERS##

#calculate checksum given sequence of bytes 
def get_checksum(precheck):
    total = 0
    precheck_int = int.from_bytes(precheck, "big")
    for i in range(0, len(precheck) * 8, 16): #want to range over the number of bits
        word = (precheck_int >> i) & 0xFFFF 
        total += word
        total = (total & 0xFFFF) + (total >> 16) #add the overflow
    #add overflow
    checksum = ~total & 0xFFFF #take complement and ensure only 16 bits
    return checksum

#break seg into 16 bits and add
#return true if valid, false if bits flipped 
def verify_checksum(seg):
    total = 0
    seg_int = int.from_bytes(seg, "big")
    for i in range(0, len(seg)*8, 16):
        word = (seg_int >> i) & 0xFFFF
        total += word
    return (total == 0) 
